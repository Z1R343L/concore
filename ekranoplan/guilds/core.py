from datetime import datetime, timezone

import orjson
from blacksheep import Request
from blacksheep.server.controllers import Controller, post

from ..checks import validate_user
from ..database import (
    Guild,
    GuildChannel,
    Member,
    Message,
    UserType,
    to_dict,
)
from ..errors import BadData
from ..randoms import get_bucket, get_welcome_content, snowflake
from ..redis_manager import guild_event
from ..utils import AuthHeader, jsonify


class GuildsCore(Controller):
    @post('/guilds')
    async def create_guild(self, auth: AuthHeader, request: Request):
        # TODO: Generate a channel: "general" and category: "General" with a welcome message
        me = validate_user(auth.value, True)

        guilds = Member.objects(Member.id == me['id']).all()

        if len(guilds) == 200:
            raise BadData()

        data: dict = await request.json(orjson.loads)
        guild_id = snowflake()
        me_usertype = UserType(**dict(me.items()))

        inserted_data = {
            'id': guild_id,
            'name': str(data['name'])[:40],
            'description': str(data.get('description', '')),
            'owner_id': me['id'],
            'nsfw': bool(data.get('nsfw', False)),
            'perferred_locale': me['locale'],
        }

        original_member = {
            'id': me['id'],
            'guild_id': guild_id,
            'owner': True,
            'user': me_usertype,
        }

        parent_id = snowflake()
        default_category_channel = {
            'id': parent_id,
            'guild_id': guild_id,
            'name': 'Text Channels',
            'parent_id': 0,
            'position': 0,
        }

        text_channel = snowflake()
        default_text_channel = {
            'id': text_channel,
            'guild_id': guild_id,
            'name': 'general',
            'parent_id': parent_id,
            'position': 1,
            'type': 1,
        }

        default_message = {
            'id': me['id'],
            'channel_id': text_channel,
            'bucket_id': get_bucket(text_channel),
            'guild_id': guild_id,
            'author': me_usertype,
            'content': get_welcome_content(user_id=me.id),
            'created_at': datetime.now(timezone.utc),
            'mentions': set([me_usertype]),
        }

        guild = Guild.create(**inserted_data)
        member = Member.create(
            **original_member, user=UserType(**dict(me))
        )
        channels = []
        messages = []
        channels.append(
            to_dict(GuildChannel.create(**default_category_channel))
        )
        channels.append(
            to_dict(GuildChannel.create(**default_text_channel))
        )
        messages.append(to_dict(Message.create(**default_message)))

        guild = to_dict(guild)
        member = to_dict(member)

        guild['members'] = member
        guild['channels'] = channels
        guild['messages'] = messages

        await guild_event(
            None,
            guild_id=guild['id'],
            data=guild,
            user_id=member['id'],
        )

        return jsonify(guild)