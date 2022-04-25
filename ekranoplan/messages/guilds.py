import orjson
from blacksheep import Request
from blacksheep.server.controllers import (
    Controller,
    delete,
    get,
    patch,
    post,
)

from ..checks import search_messages, validate_channel
from ..database import GuildChannelPin, Message, _get_date, to_dict
from ..errors import BadData, Forbidden
from ..randoms import get_bucket, snowflake
from ..redis_manager import channel_event
from ..utils import AuthHeader, jsonify


class GuildMessages(Controller):
    @get(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/messages/{int:message_id}',
    )
    async def get_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        auth: AuthHeader,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='read_message_history',
        )

        msg = search_messages(
            channel_id=channel.id, message_id=message_id
        )

        return jsonify(to_dict(msg))

    @get(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/messages',
    )
    async def get_guild_channel_messages(
        self,
        guild_id: int,
        channel_id: int,
        auth: AuthHeader,
        request: Request,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='read_message_history',
        )

        limit = int(request.query.get('limit', '50'))

        if limit > 10000:
            raise BadData()

        _msgs = search_messages(channel_id=channel.id, limit=limit)
        msgs = []

        for msg in _msgs:
            msgs.append(to_dict(msg))

        return jsonify(msgs)

    @post(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/messages',
    )
    async def create_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        request: Request,
        auth: AuthHeader,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='send_messages',
        )

        d: dict = await request.json(orjson.loads)

        if '@everyone' in d['content']:
            mentions_everyone = (
                True if perms.mention_everyone else False
            )
        else:
            mentions_everyone = False

        if d.get('referenced_message_id'):
            referenced_message = search_messages(
                channel_id=channel_id,
                message_id=int(d.pop('referenced_message_id')),
            )

        if referenced_message is None:
            raise BadData()

        data = {
            'id': snowflake(),
            'channel_id': channel_id,
            'bucket_id': get_bucket(channel_id),
            'guild_id': guild_id,
            'author': member.user,
            'content': str(d['content']),
            'mentions_everyone': mentions_everyone,
            'referenced_message_id': referenced_message.id,
        }

        msg = Message.create(**data)

        await channel_event(
            'CREATE',
            to_dict(channel),
            to_dict(msg),
            guild_id=guild_id,
            is_message=True,
        )

        return jsonify(to_dict(msg))

    @patch(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/messages/{int:message_id}',
    )
    async def edit_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        auth: AuthHeader,
        request: Request,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission=None,
        )

        msg = search_messages(
            channel_id=channel.id, message_id=message_id
        )

        if msg is None:
            raise BadData()

        if msg.author.id != member.id:
            raise Forbidden()

        d: dict = await request.json(orjson.loads)

        if d.get('content'):
            msg.content = str(d.pop('content'))

        msg.last_edited = _get_date()

        msg = msg.save()

        await channel_event(
            'EDIT',
            to_dict(channel),
            to_dict(msg),
            guild_id=guild_id,
            is_message=True,
        )

        return jsonify(to_dict(msg))

    @delete(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/messages/{int:message_id}',
    )
    async def delete_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        auth: AuthHeader,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='manage_messages',
        )

        msg = search_messages(
            channel_id=channel.id, message_id=message_id
        )

        if msg is None:
            raise BadData()

        if msg.pinned:
            pin: GuildChannelPin = GuildChannelPin.objects(
                GuildChannelPin.channel_id == channel_id,
                GuildChannelPin.message_id == message_id,
            ).get()
            pin.delete()
            await channel_event(
                'UNPIN',
                to_dict(channel),
                {
                    'guild_id': guild_id,
                    'channel_id': channel_id,
                    'message_id': message_id,
                },
                guild_id=guild_id,
                is_message=True,
            )

        msg.delete()

        r = jsonify([])
        r.status_code = 204

        await channel_event(
            'DELETE',
            to_dict(channel),
            {
                'id': message_id,
                'channel_id': channel_id,
                'guild_id': guild_id,
            },
            guild_id=guild_id,
            is_message=True,
        )

        return r

    @post(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/pins/{int:message_id}',
    )
    async def pin_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        auth: AuthHeader,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='manage_channel_pins',
        )

        msg = search_messages(
            channel_id=channel.id, message_id=message_id
        )

        if msg is None:
            raise BadData()

        msg.pinned = True

        possibly_not_empty = GuildChannelPin.objects(
            GuildChannelPin.channel_id == channel_id,
            GuildChannelPin.message_id == message_id,
        )

        if possibly_not_empty.all() != []:
            raise BadData()

        pin = GuildChannelPin.create(
            channel_id=channel_id, message_id=message_id
        )
        msg = msg.save()

        ret = {
            'pinned_data': to_dict(pin),
            'message_pinned': to_dict(msg),
        }

        await channel_event(
            'PIN',
            to_dict(channel),
            ret,
            guild_id=guild_id,
            is_message=True,
        )

        return jsonify(ret)

    @delete(
        '/guilds/{int:guild_id}/channels/{int:channel_id}/pins/{int:message_id}',
    )
    async def unpin_guild_channel_message(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        auth: AuthHeader,
    ):
        member, user, channel, perms = validate_channel(
            token=auth.value,
            guild_id=guild_id,
            channel_id=channel_id,
            permission='manage_channel_pins',
        )

        msg = search_messages(
            channel_id=channel.id, message_id=message_id
        )

        if msg is None or not msg.pinned:
            raise BadData()

        msg.pinned = False
        pin: GuildChannelPin = GuildChannelPin.objects(
            GuildChannelPin.channel_id == channel_id,
            GuildChannelPin.message_id == message_id,
        ).get()
        pin.delete()
        msg.save()

        r = jsonify([])
        r.status_code = 204

        await channel_event(
            'UNPIN',
            to_dict(channel),
            {
                'guild_id': guild_id,
                'channel_id': channel_id,
                'message_id': message_id,
            },
            guild_id=guild_id,
            is_message=True,
        )

        return jsonify(r)