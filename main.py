import sqlite3
import random
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import At
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.star import StarTools
from astrbot.api import logger
import asyncio

# --- 静态游戏数据定义 ---
# 定义了所有可用的宠物类型及其基础属性、进化路径和图片资源
PET_TYPES = {
    "水灵灵": {
        "attribute": "水",
        "description": "由纯净之水汇聚而成的元素精灵，性格温和，防御出众。",
        "initial_stats": {"attack": 8, "defense": 12},
        "evolutions": {
            1: {"name": "水灵灵", "image": "WaterSprite_1.png", "evolve_level": 30},
            2: {"name": "源流之精", "image": "WaterSprite_2.png", "evolve_level": None}
        }
    },
    "火小犬": {
        "attribute": "火",
        "description": "体内燃烧着不灭之火的幼犬，活泼好动，攻击性强。",
        "initial_stats": {"attack": 12, "defense": 8},
        "evolutions": {
            1: {"name": "火小犬", "image": "FirePup_1.png", "evolve_level": 30},
            2: {"name": "烈焰魔犬", "image": "FirePup_2.png", "evolve_level": None}
        }
    },
    "草叶猫": {
        "attribute": "草",
        "description": "能进行光合作用的奇特猫咪，攻守均衡，喜欢打盹。",
        "initial_stats": {"attack": 10, "defense": 10},
        "evolutions": {
            1: {"name": "草叶猫", "image": "LeafyCat_1.png", "evolve_level": 30},
            2: {"name": "丛林之王", "image": "LeafyCat_2.png", "evolve_level": None}
        }
    }
}
# --- 静态游戏数据定义 (商店) ---
SHOP_ITEMS = {
    "普通口粮": {"price": 10, "type": "food", "satiety": 20, "mood": 5, "description": "能快速填饱肚子的基础食物。"},
    "美味罐头": {"price": 30, "type": "food", "satiety": 50, "mood": 15, "description": "营养均衡，宠物非常爱吃。"},
    "心情饼干": {"price": 25, "type": "food", "satiety": 10, "mood": 30, "description": "能让宠物心情愉悦的神奇零食。"},
}
# --- 静态游戏数据定义 (状态中文名映射) ---
STAT_MAP = {
    "exp": "经验值",
    "mood": "心情值",
    "satiety": "饱食度"
}


@register(
    "简易群宠物游戏",
    "DITF16",
    "一个简单的的群内宠物养成插件，支持LLM随机事件、PVP对决和图片状态卡。",
    "1.2",
    "https://github.com/DITF16/astrbot_plugin_pet"
)
class PetPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_pet")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir = Path(__file__).parent / "assets"
        self.db_path = self.data_dir / "pets.db"
        self.pending_discards = {}
        self._init_database()
        logger.info("群宠物对决版插件(v1.2)已加载。")

    def _init_database(self):
        """初始化数据库，创建宠物表。"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pets (
                    user_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    pet_name TEXT NOT NULL,
                    pet_type TEXT NOT NULL,
                    level INTEGER DEFAULT 1,
                    exp INTEGER DEFAULT 0,
                    mood INTEGER DEFAULT 100,
                    satiety INTEGER DEFAULT 80,
                    attack INTEGER DEFAULT 10,
                    defense INTEGER DEFAULT 10,
                    evolution_stage INTEGER DEFAULT 1,
                    last_fed_time TEXT,
                    last_walk_time TEXT,
                    last_duel_time TEXT,
                    PRIMARY KEY (user_id, group_id)
                )
            """)

            self._add_column(cursor, 'pets', 'money', 'INTEGER DEFAULT 50')
            self._add_column(cursor, 'pets', 'last_updated_time', 'TEXT')
            self._add_column(cursor, 'pets', 'last_signin_time', 'TEXT')

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    user_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    PRIMARY KEY (user_id, group_id, item_name)
                )
            """)
            conn.commit()

    def _add_column(self, cursor, table_name, column_name, column_type):
        """辅助函数，用于向表中安全地添加列。"""
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        except sqlite3.OperationalError as e:
            if f"duplicate column name: {column_name}" not in str(e):
                raise

    def _get_pet(self, user_id: str, group_id: str) -> dict | None:
        """根据ID获取宠物信息，并自动处理离线期间的状态衰减。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pets WHERE user_id = ? AND group_id = ?", (int(user_id), int(group_id)))
            row = cursor.fetchone()
            if not row:
                return None

            pet_dict = dict(row)
            now = datetime.now()
            last_updated_str = pet_dict.get('last_updated_time')

            if not last_updated_str:
                last_updated_time = now
                cursor.execute("UPDATE pets SET last_updated_time = ? WHERE user_id = ? AND group_id = ?",
                               (now.isoformat(), int(user_id), int(group_id)))
            else:
                last_updated_time = datetime.fromisoformat(last_updated_str)

            hours_passed = (now - last_updated_time).total_seconds() / 3600
            if hours_passed >= 1:
                hours_to_decay = int(hours_passed)
                satiety_decay = 3 * hours_to_decay
                mood_decay = 2 * hours_to_decay
                new_satiety = max(0, int(pet_dict['satiety']) - satiety_decay)
                new_mood = max(0, int(pet_dict['mood']) - mood_decay)
                cursor.execute(
                    "UPDATE pets SET satiety = ?, mood = ?, last_updated_time = ? WHERE user_id = ? AND group_id = ?",
                    (new_satiety, new_mood, now.isoformat(), int(user_id), int(group_id))
                )
                logger.info(
                    f"宠物 {pet_dict['pet_name']} 离线{hours_to_decay}小时，饱食度降低{satiety_decay}, 心情降低{mood_decay}")
                pet_dict['satiety'] = new_satiety
                pet_dict['mood'] = new_mood

            conn.commit()
            return pet_dict

    def _exp_for_next_level(self, level: int) -> int:
        """计算升到下一级所需的总经验。"""
        return int(10 * (level ** 1.5))

    def _check_level_up(self, user_id: str, group_id: str) -> list[str]:
        """检查并处理宠物升级，返回一个包含升级消息的列表。"""
        level_up_messages = []
        while True:
            pet = self._get_pet(user_id, group_id)
            if not pet: break

            exp_needed = self._exp_for_next_level(pet['level'])
            if pet['exp'] >= exp_needed:
                new_level = pet['level'] + 1
                remaining_exp = pet['exp'] - exp_needed
                new_attack = pet['attack'] + random.randint(1, 2)
                new_defense = pet['defense'] + random.randint(1, 2)

                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE pets SET level = ?, exp = ?, attack = ?, defense = ? WHERE user_id = ? AND group_id = ?",
                        (new_level, remaining_exp, new_attack, new_defense, int(user_id), int(group_id))
                    )
                    conn.commit()

                logger.info(f"宠物升级: {pet['pet_name']} 升到了 {new_level} 级！")
                level_up_messages.append(f"🎉 恭喜！你的宠物「{pet['pet_name']}」升级到了 Lv.{new_level}！")
            else:
                break
        return level_up_messages

    def _generate_pet_status_image(self, pet_data: dict, sender_name: str) -> Path | str:
        """根据宠物数据生成一张状态图。"""
        try:
            W, H = 800, 600
            bg_path = self.assets_dir / "background.png"
            font_path = self.assets_dir / "font.ttf"
            img = Image.open(bg_path).resize((W, H))
            draw = ImageDraw.Draw(img)
            font_title = ImageFont.truetype(str(font_path), 40)
            font_text = ImageFont.truetype(str(font_path), 28)

            pet_type_info = PET_TYPES[pet_data['pet_type']]
            evo_info = pet_type_info['evolutions'][pet_data['evolution_stage']]
            pet_img_path = self.assets_dir / evo_info['image']
            pet_img = Image.open(pet_img_path).convert("RGBA").resize((300, 300))
            img.paste(pet_img, (50, 150), pet_img)

            draw.text((W / 2, 50), f"{pet_data['pet_name']}的状态", font=font_title, fill="white", anchor="mt")
            draw.text((400, 150), f"主人: {sender_name}", font=font_text, fill="white")
            draw.text((400, 200), f"种族: {evo_info['name']} ({pet_data['pet_type']})", font=font_text, fill="white")
            draw.text((400, 250), f"等级: Lv.{pet_data['level']}", font=font_text, fill="white")

            exp_needed = self._exp_for_next_level(pet_data['level'])
            exp_ratio = min(1.0, pet_data['exp'] / exp_needed)
            draw.text((400, 300), f"经验: {pet_data['exp']} / {exp_needed}", font=font_text, fill="white")
            draw.rectangle([400, 340, 750, 360], outline="white", fill="gray")
            draw.rectangle([400, 340, 400 + 350 * exp_ratio, 360], fill="#66ccff")

            draw.text((400, 390), f"攻击: {pet_data['attack']}", font=font_text, fill="white")
            draw.text((600, 390), f"防御: {pet_data['defense']}", font=font_text, fill="white")
            draw.text((400, 440), f"心情: {pet_data['mood']}/100", font=font_text, fill="white")
            draw.text((600, 440), f"饱食度: {pet_data['satiety']}/100", font=font_text, fill="white")
            draw.text((400, 490), f"金钱: ${pet_data.get('money', 0)}", font=font_text, fill="#FFD700")

            output_path = self.cache_dir / f"status_{pet_data['group_id']}_{pet_data['user_id']}.png"
            img.save(output_path, format='PNG')
            return output_path
        except FileNotFoundError as e:
            logger.error(f"生成状态图失败，缺少素材文件: {e}")
            return f"生成状态图失败，请检查插件素材文件是否完整：{e}"
        except Exception as e:
            logger.error(f"生成状态图时发生未知错误: {e}")
            return f"生成状态图时发生未知错误: {e}"

    def _get_attribute_multiplier(self, attacker_attr: str, defender_attr: str) -> float:
        """计算属性克制伤害倍率。"""
        effectiveness = {"水": "火", "火": "草", "草": "水"}
        if effectiveness.get(attacker_attr) == defender_attr: return 1.2
        if effectiveness.get(defender_attr) == attacker_attr: return 0.8
        return 1.0

    def _run_battle(self, pet1: dict, pet2: dict) -> tuple[list[str], str]:
        """执行两个宠物之间的对战，集成状态、暴击和等级压制逻辑。"""
        log = []
        p1_hp = pet1['level'] * 10 + 50
        p2_hp = pet2['level'] * 10 + 50
        p1_name, p2_name = pet1['pet_name'], pet2['pet_name']
        p1_attr = PET_TYPES[pet1['pet_type']]['attribute']
        p2_attr = PET_TYPES[pet2['pet_type']]['attribute']

        log.append(
            f"战斗开始！\n「{p1_name}」(Lv.{pet1['level']} {p1_attr}系) vs 「{p2_name}」(Lv.{pet2['level']} {p2_attr}系)")

        def calculate_turn(attacker, defender, defender_hp, turn_log):
            satiety_mod = 0.5 + (attacker['satiety'] / 100) * 0.7
            if attacker['satiety'] < 20:
                turn_log.append(f"「{attacker['pet_name']}」饿得有气无力...")

            eff_attack = attacker['attack'] * satiety_mod
            eff_defense = defender['defense'] * (0.5 + (defender['satiety'] / 100) * 0.7)

            crit_chance = 0.05 + (attacker['mood'] / 100) * 0.20
            is_crit = random.random() < crit_chance
            crit_multiplier = 1.3 + (attacker['mood'] / 100) * 0.4

            attr_multiplier = self._get_attribute_multiplier(
                PET_TYPES[attacker['pet_type']]['attribute'],
                PET_TYPES[defender['pet_type']]['attribute']
            )
            level_diff_mod = 1 + (attacker['level'] - defender['level']) * 0.02
            base_dmg = max(1, eff_attack * random.uniform(0.9, 1.1) - eff_defense * 0.6)

            final_dmg = int(base_dmg * attr_multiplier * level_diff_mod)
            if is_crit:
                final_dmg = int(final_dmg * crit_multiplier)

            new_defender_hp = defender_hp - final_dmg

            turn_log.append(f"「{attacker['pet_name']}」发起了攻击！")
            if is_crit: turn_log.append("💥 会心一击！")
            if attr_multiplier > 1.0:
                turn_log.append("效果拔群！")
            elif attr_multiplier < 1.0:
                turn_log.append("效果不太理想…")
            turn_log.append(f"对「{defender['pet_name']}」造成了 {final_dmg} 点伤害！(剩余HP: {max(0, new_defender_hp)})")

            return new_defender_hp

        turn = 0
        while p1_hp > 0 and p2_hp > 0:
            turn += 1
            log.append(f"\n--- 第 {turn} 回合 ---")

            turn_log_1 = []
            p2_hp = calculate_turn(pet1, pet2, p2_hp, turn_log_1)
            log.extend(turn_log_1)
            if p2_hp <= 0: break

            turn_log_2 = []
            p1_hp = calculate_turn(pet2, pet1, p1_hp, turn_log_2)
            log.extend(turn_log_2)

        winner_name = p1_name if p1_hp > 0 else p2_name
        log.append(f"\n战斗结束！胜利者是「{winner_name}」！")
        return log, winner_name

    def _extract_json_from_text(self, text: str) -> str | None:
        """从文本中稳健地提取JSON对象。"""
        match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        if match:
            return match.group(1).strip()
        try:
            start_index = text.find('{')
            if start_index == -1: return None
            brace_level = 0
            for i, char in enumerate(text[start_index:]):
                if char == '{':
                    brace_level += 1
                elif char == '}':
                    brace_level -= 1
                if brace_level == 0: return text[start_index: start_index + i + 1]
            return None
        except Exception:
            return None

    @filter.command("领养宠物")
    async def adopt_pet(self, event: AstrMessageEvent, pet_name: str | None = None):
        """领养一只随机的初始宠物"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("该功能仅限群聊使用。")
            return

        if self._get_pet(user_id, group_id):
            yield event.plain_result("你在这个群里已经有一只宠物啦！发送 /我的宠物 查看。")
            return

        type_name = random.choice(["水灵灵", "火小犬", "草叶猫"])
        if not pet_name: pet_name = type_name

        pet_info = PET_TYPES[type_name]
        stats = pet_info['initial_stats']
        now_iso = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO pets (user_id, group_id, pet_name, pet_type, attack, defense, last_updated_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(user_id), int(group_id), pet_name, type_name, stats['attack'], stats['defense'], now_iso)
            )
            conn.commit()
        logger.info(f"新宠物领养: 群 {group_id} 用户 {user_id} 领养了 {type_name} - {pet_name}")
        yield event.plain_result(
            f"恭喜你，{event.get_sender_name()}！命运让你邂逅了「{pet_name}」({type_name})！\n发送 /我的宠物 查看它的状态吧。")

    @filter.command("我的宠物")
    async def my_pet_status(self, event: AstrMessageEvent):
        """查看宠物状态"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return
        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物哦，快发送 /领养宠物 来选择一只吧！")
            return
        result = self._generate_pet_status_image(pet, event.get_sender_name())
        if isinstance(result, Path):
            yield event.image_result(str(result))
        else:
            yield event.plain_result(result)

    @filter.command("宠物改名")
    async def rename_pet(self, event: AstrMessageEvent, new_name: str | None = None):
        """为你的宠物改一个新名字。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return
        if not new_name:
            yield event.plain_result("请提供一个新名字。用法: /宠物改名 [新名字]")
            return
        if not 1 <= len(new_name) <= 10:
            yield event.plain_result("宠物的名字长度必须在1到10个字符之间。")
            return
        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，不能改名哦。")
            return
        old_name = pet['pet_name']
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE pets SET pet_name = ? WHERE user_id = ? AND group_id = ?",
                         (new_name, int(user_id), int(group_id)))
            conn.commit()
        logger.info(f"宠物改名: 群 {group_id} 用户 {user_id} 将 {old_name} 改名为 {new_name}")
        yield event.plain_result(f"改名成功！你的宠物「{old_name}」现在叫做「{new_name}」了。")

    @filter.command("散步")
    async def walk_pet(self, event: AstrMessageEvent):
        """带宠物散步，触发LLM生成的奇遇或PVE战斗"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，不能去散步哦。")
            return

        now = datetime.now()
        last_walk_str = pet.get('last_walk_time')
        if last_walk_str and now - datetime.fromisoformat(last_walk_str) < timedelta(minutes=5):
            yield event.plain_result(f"刚散步回来，让「{pet['pet_name']}」休息一下吧。")
            return

        final_reply = []
        if random.random() < 0.7:
            prompt = (
                f"你是一个宠物游戏的世界事件生成器。请为一只名为'{pet['pet_name']}'的宠物在散步时，"
                "生成一个简短、有趣的随机奇遇故事（50字以内）。"
                "然后，将奖励信息封装成一个JSON对象，并使用markdown的json代码块返回。JSON应包含四个字段："
                "\"description\" (string, 故事描述), "
                "\"reward_type\" (string, 从 'exp', 'mood', 'satiety' 中随机选择), "
                "\"reward_value\" (integer, 奖励数值), "
                "和 \"money_gain\" (integer, 获得的金钱)。\n\n"
                "示例回复格式：\n"
                "这是一个奇妙的下午。\n"
                "```json\n"
                "{\n"
                "    \"description\": \"{pet_name}在河边发现了一颗闪亮的石头，心情大好！\",\n"
                "    \"reward_type\": \"mood\",\n"
                "    \"reward_value\": 15,\n"
                "    \"money_gain\": 5\n"
                "}\n"
                "```"
            )

            completion_text = ""
            try:
                llm_response = await self.context.get_using_provider().text_chat(prompt=prompt)
                completion_text = llm_response.completion_text
                json_str = self._extract_json_from_text(completion_text)

                if not json_str:
                    logger.error(f"无法从LLM响应中提取JSON: {completion_text}")
                    raise ValueError("未能解析LLM的响应格式")

                data = json.loads(json_str)
                desc = data['description'].format(pet_name=pet['pet_name'])
                reward_type = data['reward_type']
                reward_value = int(data['reward_value'])
                money_gain = int(data.get('money_gain', 0))

                reward_type_chinese = STAT_MAP.get(reward_type, reward_type)
                final_reply.append(f"奇遇发生！\n{desc}\n你的宠物获得了 {reward_value} 点{reward_type_chinese}！")
                if money_gain > 0:
                    final_reply.append(f"意外之喜！你在路边捡到了 ${money_gain}！")

                with sqlite3.connect(self.db_path) as conn:
                    update_query = (
                        f"UPDATE pets SET "
                        f"{reward_type} = {'MIN(100, ' + reward_type + ' + ?)' if reward_type != 'exp' else (reward_type + ' + ?')}, "
                        f"money = money + ?, "
                        f"last_walk_time = ? "
                        f"WHERE user_id = ? AND group_id = ?"
                    )
                    conn.execute(update_query, (reward_value, money_gain, now.isoformat(), int(user_id), int(group_id)))
                    conn.commit()

                if reward_type == 'exp':
                    final_reply.extend(self._check_level_up(user_id, group_id))
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error(f"LLM奇遇事件处理失败: {e}\n原始返回: {completion_text}")
                final_reply.append("你的宠物在外面迷路了，好在最后成功找回，但什么也没发生。")
        else:
            npc_level = max(1, pet['level'] + random.randint(-1, 1))
            npc_type_name = random.choice(list(PET_TYPES.keys()))
            npc_stats = PET_TYPES[npc_type_name]['initial_stats']
            npc_pet = {
                "pet_name": f"野生的{npc_type_name}", "pet_type": npc_type_name,
                "level": npc_level, "attack": npc_stats['attack'] + npc_level,
                "defense": npc_stats['defense'] + npc_level, "satiety": 100, "mood": 100
            }

            battle_log, winner_name = self._run_battle(pet, npc_pet)
            final_reply.extend(battle_log)

            exp_gain = 0
            money_gain = 0
            if winner_name == pet['pet_name']:
                exp_gain = npc_level * 5 + random.randint(1, 5)
                money_gain = random.randint(5, 15)
                final_reply.append(f"\n胜利了！你获得了 {exp_gain} 点经验值和 ${money_gain} 赏金！")
            else:
                exp_gain = 1
                final_reply.append(f"\n很遗憾，你的宠物战败了，但也获得了 {exp_gain} 点经验。")

            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE pets SET exp = exp + ?, money = money + ?, last_walk_time = ? WHERE user_id = ? AND group_id = ?",
                    (exp_gain, money_gain, now.isoformat(), int(user_id), int(group_id)))
                conn.commit()
            final_reply.extend(self._check_level_up(user_id, group_id))

        yield event.plain_result("\n".join(final_reply))

    @filter.command("对决")
    async def duel_pet(self, event: AiocqhttpMessageEvent):
        """与其他群友的宠物进行对决"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        target_id = self.get_at(event)
        if not target_id:
            yield event.plain_result("请@一位你想对决的群友。用法: /对决 @某人")
            return

        challenger_pet = self._get_pet(user_id, group_id)
        if not challenger_pet:
            yield event.plain_result("你还没有宠物，无法发起对决。")
            return

        if user_id == target_id:
            yield event.plain_result("不能和自己对决哦。")
            return

        target_pet = self._get_pet(target_id, group_id)
        if not target_pet:
            yield event.plain_result("对方还没有宠物呢。")
            return

        now = datetime.now()
        last_duel_challenger_str = challenger_pet.get('last_duel_time')
        if last_duel_challenger_str:
            last_duel_challenger = datetime.fromisoformat(last_duel_challenger_str)
            if now - last_duel_challenger < timedelta(minutes=30):
                remaining = timedelta(minutes=30) - (now - last_duel_challenger)
                yield event.plain_result(f"你的对决技能正在冷却中，还需等待 {str(remaining).split('.')[0]}。")
                return

        battle_log, winner_name = self._run_battle(challenger_pet, target_pet)

        money_gain = 20
        if winner_name == challenger_pet['pet_name']:
            winner_id, loser_id = user_id, target_id
            winner_exp = 10 + target_pet['level'] * 2
            loser_exp = 5 + challenger_pet['level']
        else:
            winner_id, loser_id = target_id, user_id
            winner_exp = 10 + challenger_pet['level'] * 2
            loser_exp = 5 + target_pet['level']

        final_reply = list(battle_log)
        final_reply.append(
            f"\n对决结算：胜利者获得了 {winner_exp} 点经验值和 ${money_gain}，参与者获得了 {loser_exp} 点经验值。")

        with sqlite3.connect(self.db_path) as conn:
            now_iso = now.isoformat()
            conn.execute("UPDATE pets SET last_duel_time = ? WHERE user_id = ? AND group_id = ?",
                         (now_iso, int(user_id), int(group_id)))
            conn.execute("UPDATE pets SET last_duel_time = ? WHERE user_id = ? AND group_id = ?",
                         (now_iso, int(target_id), int(group_id)))
            conn.execute("UPDATE pets SET money = money + ? WHERE user_id = ? AND group_id = ?",
                         (money_gain, int(winner_id), int(group_id)))
            conn.execute("UPDATE pets SET exp = exp + ? WHERE user_id = ? AND group_id = ?",
                         (winner_exp, int(winner_id), int(group_id)))
            conn.execute("UPDATE pets SET exp = exp + ? WHERE user_id = ? AND group_id = ?",
                         (loser_exp, int(loser_id), int(group_id)))
            conn.commit()

        final_reply.extend(self._check_level_up(winner_id, group_id))
        final_reply.extend(self._check_level_up(loser_id, group_id))

        yield event.plain_result("\n".join(final_reply))

    @filter.command("宠物进化")
    async def evolve_pet(self, event: AstrMessageEvent):
        """让达到条件的宠物进化。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物哦。")
            return

        pet_type_info = PET_TYPES[pet['pet_type']]
        current_evo_info = pet_type_info['evolutions'][pet['evolution_stage']]

        evolve_level = current_evo_info['evolve_level']
        if not evolve_level:
            yield event.plain_result(f"「{pet['pet_name']}」已是最终形态，无法再进化。")
            return

        if pet['level'] < evolve_level:
            yield event.plain_result(f"「{pet['pet_name']}」需达到 Lv.{evolve_level} 才能进化。")
            return

        next_evo_stage = pet['evolution_stage'] + 1
        next_evo_info = pet_type_info['evolutions'][next_evo_stage]
        new_attack = pet['attack'] + random.randint(8, 15)
        new_defense = pet['defense'] + random.randint(8, 15)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE pets SET evolution_stage = ?, attack = ?, defense = ? WHERE user_id = ? AND group_id = ?",
                (next_evo_stage, new_attack, new_defense, int(user_id), int(group_id)))
            conn.commit()

        logger.info(f"宠物进化成功: {pet['pet_name']} -> {next_evo_info['name']}")
        yield event.plain_result(
            f"光芒四射！你的「{pet['pet_name']}」成功进化为了「{next_evo_info['name']}」！各项属性都得到了巨幅提升！")

    @filter.command("宠物商店")
    async def shop(self, event: AstrMessageEvent):
        """显示宠物商店中可购买的物品列表。"""
        reply = "欢迎光临宠物商店！\n--------------------\n"
        for name, item in SHOP_ITEMS.items():
            reply += f"【{name}】 ${item['price']}\n效果: {item['description']}\n"
        reply += "--------------------\n使用 `/购买 [物品名] [数量]` 来购买。"
        yield event.plain_result(reply)

    @filter.command("宠物背包")
    async def backpack(self, event: AstrMessageEvent):
        """显示你的宠物背包中的物品。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not self._get_pet(user_id, group_id):
            yield event.plain_result("你还没有宠物，自然也没有背包啦。")
            return

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ? AND group_id = ?",
                           (int(user_id), int(group_id)))
            items = cursor.fetchall()

        if not items:
            yield event.plain_result("你的背包空空如也，去商店看看吧！")
            return

        reply = f"{event.get_sender_name()}的背包:\n--------------------\n"
        for item_name, quantity in items:
            reply += f"【{item_name}】 x {quantity}\n"
        yield event.plain_result(reply)

    @filter.command("购买")
    async def buy_item(self, event: AstrMessageEvent, item_name: str, quantity: int = 1):
        """从商店购买物品。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        if item_name not in SHOP_ITEMS:
            yield event.plain_result(f"商店里没有「{item_name}」这种东西。")
            return

        if not self._get_pet(user_id, group_id):
            yield event.plain_result("你还没有宠物，无法购买物品。")
            return

        item_info = SHOP_ITEMS[item_name]
        total_cost = item_info['price'] * quantity

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pets SET money = money - ? WHERE user_id = ? AND group_id = ? AND money >= ?",
                (total_cost, int(user_id), int(group_id), total_cost)
            )

            if cursor.rowcount == 0:
                yield event.plain_result(f"你的钱不够哦！购买 {quantity} 个「{item_name}」需要 ${total_cost}。")
                return

            cursor.execute("""
                    INSERT INTO inventory (user_id, group_id, item_name, quantity) 
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, group_id, item_name) 
                    DO UPDATE SET quantity = quantity + excluded.quantity
                """, (int(user_id), int(group_id), item_name, quantity))
            conn.commit()

        yield event.plain_result(f"购买成功！你花费 ${total_cost} 购买了 {quantity} 个「{item_name}」。")

    @filter.command("投喂")
    async def feed_pet_item(self, event: AstrMessageEvent, item_name: str):
        """从背包中使用食物投喂宠物。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，不能进行投喂哦。")
            return

        if item_name not in SHOP_ITEMS or SHOP_ITEMS[item_name].get('type') != 'food':
            yield event.plain_result(f"「{item_name}」不是可以投喂的食物。")
            return

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND group_id = ? AND item_name = ? AND quantity > 0",
                (int(user_id), int(group_id), item_name)
            )

            if cursor.rowcount == 0:
                yield event.plain_result(f"你的背包里没有「{item_name}」。")
                return

            item_info = SHOP_ITEMS[item_name]
            satiety_gain = item_info.get('satiety', 0)
            mood_gain = item_info.get('mood', 0)

            cursor.execute(
                "UPDATE pets SET satiety = MIN(100, satiety + ?), mood = MIN(100, mood + ?) WHERE user_id = ? AND group_id = ?",
                (satiety_gain, mood_gain, int(user_id), int(group_id))
            )
            cursor.execute(
                "DELETE FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ? AND quantity <= 0",
                (int(user_id), int(group_id), item_name))
            conn.commit()

        satiety_chinese = STAT_MAP.get('satiety', '饱食度')
        mood_chinese = STAT_MAP.get('mood', '心情值')
        yield event.plain_result(
            f"你给「{pet['pet_name']}」投喂了「{item_name}」，它的{satiety_chinese}增加了 {satiety_gain}，{mood_chinese}增加了 {mood_gain}！")

    @filter.command("宠物签到")
    async def daily_signin(self, event: AstrMessageEvent):
        """每日签到领取奖励。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，无法签到。")
            return

        now = datetime.now()
        last_signin_str = pet.get('last_signin_time')
        if last_signin_str:
            last_signin_time = datetime.fromisoformat(last_signin_str)
            if last_signin_time.date() == now.date():
                yield event.plain_result("今天已经签过到了，明天再来吧！")
                return

        money_gain = random.randint(15, 50)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE pets SET money = money + ?, last_signin_time = ? WHERE user_id = ? AND group_id = ?",
                         (money_gain, now.isoformat(), int(user_id), int(group_id)))
            conn.commit()

        yield event.plain_result(f"签到成功！你获得了 ${money_gain}！")

    @filter.command("宠物排行")
    async def pet_ranking(self, event: AstrMessageEvent):
        """查看本群的宠物排行榜。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT pet_name, level, exp FROM pets WHERE group_id = ? ORDER BY level DESC, exp DESC LIMIT 5",
                (int(group_id),))
            rankings = cursor.fetchall()

        if not rankings:
            yield event.plain_result("本群还没有宠物，快去领养一只争夺第一吧！")
            return

        reply = "🏆 本群宠物排行榜 🏆\n--------------------\n"
        medals = ["🥇", "🥈", "🥉", "🏅", "🏅"]
        for i, row in enumerate(rankings):
            reply += f"{medals[i]} 「{row['pet_name']}」 - Lv.{row['level']} (EXP: {row['exp']})\n"

        yield event.plain_result(reply)

    @filter.command("丢弃宠物")
    async def discard_pet_request(self, event: AstrMessageEvent):
        """发起丢弃宠物的请求。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        if not self._get_pet(user_id, group_id):
            yield event.plain_result("你都没有宠物，丢弃什么呢？")
            return

        self.pending_discards[(user_id, group_id)] = datetime.now() + timedelta(seconds=30)
        yield event.plain_result(f"⚠️警告！你确定要丢弃你的宠物吗？此操作不可逆！\n请在30秒内发送 `/确认丢弃` 来完成操作。")

    @filter.command("确认丢弃")
    async def confirm_discard_pet(self, event: AstrMessageEvent):
        """确认丢弃宠物。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        request_key = (user_id, group_id)
        if request_key in self.pending_discards and datetime.now() < self.pending_discards[request_key]:
            del self.pending_discards[request_key]

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM pets WHERE user_id = ? AND group_id = ?", (int(user_id), int(group_id)))
                conn.execute("DELETE FROM inventory WHERE user_id = ? AND group_id = ?", (int(user_id), int(group_id)))
                conn.commit()

            yield event.plain_result("你的宠物已经离开了。江湖再见，或许会有新的邂逅。")
        else:
            yield event.plain_result("没有待确认的丢弃请求，或请求已超时。")

    @filter.command("宠物菜单")
    async def pet_menu(self, event: AstrMessageEvent):
        """显示所有可用的宠物插件命令。"""
        menu_text = """--- 🐾 宠物插件帮助菜单 v1.2 🐾 ---
【核心功能】
/领养宠物 [名字] - 领养一只新宠物。
/我的宠物 - 查看宠物详细状态图。
/宠物改名 [新名] - 给你的宠物换个名字。
/宠物进化 - 当宠物达到等级时进化。

【日常互动】
/宠物签到 - 每天领取金钱奖励。
/散步 - 带宠物散步，可能触发奇遇或战斗。
/投喂 [物品] - 从背包使用食物喂养宠物。

【商店与物品】
/宠物商店 - 查看可购买的商品。
/购买 [物品] [数量] - 从商店购买物品。
/宠物背包 - 查看你拥有的物品。

【社交与竞技】
/对决 @某人 - 与群友的宠物进行1v1对决。
/宠物排行 - 查看本群最强的宠物们。

【其他命令】
/丢弃宠物 - (危险) 与你的宠物告别，慎用！
"""
        yield event.plain_result(menu_text)

    @staticmethod
    def get_at(event: AiocqhttpMessageEvent) -> str | None:
        return next(
            (str(seg.qq) for seg in event.get_messages() if isinstance(seg, At) and str(seg.qq) != event.get_self_id()),
            None)

    async def terminate(self):
        """插件卸载/停用时调用。"""
        logger.info("群宠物对决版插件(v1.2)已卸载。")