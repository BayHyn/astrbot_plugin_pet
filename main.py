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
from copy import deepcopy

# --- 默认配置数据 (如果JSON文件不存在，将使用这些数据创建) ---

# --- 默认 宠物数据 (v1.5 新增 "闪电") ---
DEFAULT_PETS = {
    "水灵灵": {
        "attribute": "水",
        "description": "由纯净之水汇聚而成的元素精灵，性格温和，防御出众。",
        "base_stats": {"attack": 8, "defense": 12},
        "evolutions": {
            "1": {"name": "水灵灵", "image": "WaterSprite_1.png", "evolve_level": 30},
            "2": {"name": "源流之精", "image": "WaterSprite_2.png", "evolve_level": None}
        },
        "learnset": {
            "1": ["撞击", "水枪"],
            "5": ["抓挠"],
            "10": ["水之波动"],
            "30": ["水炮"]
        }
    },
    "火小犬": {
        "attribute": "火",
        "description": "体内燃烧着不灭之火的幼犬，活泼好动，攻击性强。",
        "base_stats": {"attack": 12, "defense": 8},
        "evolutions": {
            "1": {"name": "火小犬", "image": "FirePup_1.png", "evolve_level": 30},
            "2": {"name": "烈焰魔犬", "image": "FirePup_2.png", "evolve_level": None}
        },
        "learnset": {
            "1": ["撞击", "火花"],
            "5": ["咬住"],
            "10": ["火焰轮"],
            "30": ["喷射火焰"]
        }
    },
    "草叶猫": {
        "attribute": "草",
        "description": "能进行光合作用的奇特猫咪，攻守均衡，喜欢打盹。",
        "base_stats": {"attack": 10, "defense": 10},
        "evolutions": {
            "1": {"name": "草叶猫", "image": "LeafyCat_1.png", "evolve_level": 30},
            "2": {"name": "丛林之王", "image": "LeafyCat_2.png", "evolve_level": None}
        },
        "learnset": {
            "1": ["撞击", "飞叶快刀"],
            "5": ["抓挠"],
            "10": ["魔法叶"],
            "15": ["催眠粉"],
            "30": ["日光束"]
        }
    },
    "闪电": {
        "attribute": "电",
        "description": "一只行动迅速的宠物，浑身有着让人酥酥麻麻的电弧。",
        "base_stats": {"attack": 11, "defense": 9},
        "evolutions": {
            "1": {"name": "闪电", "image": "Lightning.jpg", "evolve_level": None}
        },
        "learnset": {
            "1": ["撞击", "电击"],
            "8": ["电光一闪"],
            "12": ["十万伏特"]
        }
    }
}

# --- 默认 技能数据 (v1.5 新增 "effect" 字段) ---
DEFAULT_MOVES = {
    "撞击": {"attribute": "普通", "power": 40, "description": "用身体猛撞对手。"},
    "抓挠": {"attribute": "普通", "power": 40, "description": "用利爪抓伤对手。"},
    "咬住": {"attribute": "普通", "power": 50, "description": "用牙齿撕咬对手。"},
    "电光一闪": {"attribute": "普通", "power": 50, "description": "高速冲向对手。"},
    "水枪": {"attribute": "水", "power": 40, "description": "向对手喷射水流。"},
    "水之波动": {"attribute": "水", "power": 60, "description": "释放水之波动攻击。"},
    "水炮": {"attribute": "水", "power": 110, "description": "威力巨大的水柱。"},
    "火花": {"attribute": "火", "power": 40, "description": "小小的火苗。"},
    "火焰轮": {"attribute": "火", "power": 60, "description": "缠绕火焰的冲撞。"},
    "喷射火焰": {"attribute": "火", "power": 90, "description": "猛烈的火焰攻击。"},
    "飞叶快刀": {"attribute": "草", "power": 40, "description": "飞出叶片切割对手。"},
    "魔法叶": {"attribute": "草", "power": 60, "description": "必定命中的神奇叶片。"},
    "日光束": {"attribute": "草", "power": 120, "description": "汇聚日光，释放光束。"},
    "催眠粉": {"attribute": "草", "power": 0, "description": "撒出催眠的粉末。", "effect": {"type": "SLEEP", "chance": 0.75}},
    "电击": {"attribute": "电", "power": 40, "description": "微弱的电击。", "effect": {"type": "PARALYSIS", "chance": 0.1}},
    "十万伏特": {"attribute": "电", "power": 90, "description": "强力的电击。", "effect": {"type": "PARALYSIS", "chance": 0.1}},
    "剧毒": {"attribute": "毒", "power": 0, "description": "让对手中剧毒。", "effect": {"type": "POISON", "chance": 1.0}}
}

# --- 默认 散步事件 ---
DEFAULT_WALK_EVENTS = [
    {"type": "reward", "weight": 20, "description": "「{pet_name}」在草丛里发现了一个被丢弃的训练沙袋，蹭了蹭，获得了经验！", "reward_type": "exp", "reward_value": [10, 20], "money_gain": 0},
    {"type": "reward", "weight": 20, "description": "「{pet_name}」追逐着一只蝴蝶，玩得不亦乐乎，心情大好！", "reward_type": "mood", "reward_value": 15, "money_gain": 0},
    {"type": "reward", "weight": 15, "description": "「{pet_name}」在树下发现了几颗野果，开心地吃掉了。", "reward_type": "satiety", "reward_value": [10, 15], "money_gain": 0},
    {"type": "reward", "weight": 10, "description": "「{pet_name}」在地上发现了一个闪闪发光的东西，原来是几枚硬币！", "reward_type": "none", "reward_value": 0, "money_gain": [15, 30]},
    {"type": "pve", "weight": 15, "description": "「{pet_name}」在散步时，突然从草丛里跳出了一只野生宠物！"},
    {"type": "minigame", "weight": 10, "description": "「{pet_name}」遇到了一个神秘人，他伸出双手说：“猜猜看，奖励在哪只手里？”", "win_chance": 0.5, "win_text": "猜对了！神秘人留下了一些金钱和食物作为奖励。", "lose_text": "猜错了...神秘人耸耸肩，消失在了雾中。", "win_reward": {"money": [20, 40], "mood": 10}},
    {"type": "nothing", "weight": 10, "description": "「{pet_name}」悠闲地散了一圈，什么特别的事情都没发生。"}
]

# --- 静态游戏数据定义 (商店) (v1.5 更新) ---
SHOP_ITEMS = {
    # 食物
    "普通口粮": {"price": 10, "type": "food", "satiety": 20, "mood": 5, "description": "能快速填饱肚子的基础食物。"},
    "美味罐头": {"price": 30, "type": "food", "satiety": 50, "mood": 15, "description": "营养均衡，宠物非常爱吃。"},
    "心情饼干": {"price": 25, "type": "food", "satiety": 10, "mood": 30, "description": "能让宠物心情愉悦的神奇零食。"},
    # 药品
    "解毒药": {"price": 40, "type": "status_heal", "cures": "POISON", "description": "治愈「中毒」状态。"},
    "苏醒药": {"price": 40, "type": "status_heal", "cures": "SLEEP", "description": "治愈「睡眠」状态。"},
    "麻痹药": {"price": 40, "type": "status_heal", "cures": "PARALYSIS", "description": "治愈「麻痹」状态。"},
    # 持有物
    "力量头带": {"price": 200, "type": "held_item", "description": "【持有】战斗时，攻击力小幅提升。"},
    "坚硬外壳": {"price": 200, "type": "held_item", "description": "【持有】战斗时，防御力小幅提升。"},
    # 技能光盘
    "技能光盘-剧毒": {"price": 500, "type": "tm", "move_name": "剧毒", "description": "一次性光盘，让宠物学会「剧毒」。"}
}
# --- 静态游戏数据定义 (状态中文名映射) (v1.5 更新) ---
STAT_MAP = {
    "exp": "经验值",
    "mood": "心情值",
    "satiety": "饱食度",
    "POISON": "中毒",
    "SLEEP": "睡眠",
    "PARALYSIS": "麻痹"
}


@register(
    "简易群宠物游戏",
    "DITF16",
    "一个简单的的群内宠物养成插件",
    "1.5",
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

        # --- JSON 配置文件路径 ---
        self.events_path = self.data_dir / "walk_events.json"
        self.pets_path = self.data_dir / "pets.json"
        self.moves_path = self.data_dir / "moves.json"

        # --- 加载配置 ---
        self.walk_events = self._load_config(self.events_path, DEFAULT_WALK_EVENTS)
        self.pets_data = self._load_config(self.pets_path, DEFAULT_PETS)
        self.moves_data = self._load_config(self.moves_path, DEFAULT_MOVES)

        self.pending_discards = {}
        self._init_database()
        logger.info("简易群宠物游戏插件(astrbot_plugin_pet)已加载。")

    def _init_database(self):
        """初始化数据库，创建宠物表和物品表。"""
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
                    money INTEGER DEFAULT 50,
                    last_updated_time TEXT,
                    last_signin_time TEXT,
                    PRIMARY KEY (user_id, group_id)
                )
            """)

            # --- 为 v1.4 添加新列 ---
            self._add_column(cursor, 'pets', 'move1', 'TEXT')
            self._add_column(cursor, 'pets', 'move2', 'TEXT')
            self._add_column(cursor, 'pets', 'move3', 'TEXT')
            self._add_column(cursor, 'pets', 'move4', 'TEXT')
            # --- 为 v1.5 添加新列 ---
            self._add_column(cursor, 'pets', 'held_item', 'TEXT')
            self._add_column(cursor, 'pets', 'status_condition', 'TEXT')

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
                logger.warning(f"尝试添加已存在的列: {column_name} (已忽略)")
            else:
                raise

    def _load_config(self, config_path: Path, default_data: dict | list) -> dict | list:
        """加载指定的JSON配置文件，如果不存在则创建。"""
        if not config_path.exists():
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_data, f, ensure_ascii=False, indent=2)
                logger.info(f"未找到配置文件，已自动创建: {config_path}")
                return default_data
            except Exception as e:
                logger.error(f"创建默认配置文件失败 {config_path}: {e}")
                return default_data
        else:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"成功加载配置文件: {config_path}")
                return data
            except json.JSONDecodeError:
                logger.error(f"配置文件 {config_path} 格式错误，将使用默认数据。")
                return default_data
            except Exception as e:
                logger.error(f"加载配置文件失败 {config_path}: {e}")
                return default_data

    def _select_walk_event(self) -> dict:
        """根据权重随机选择一个散步事件。"""
        if not self.walk_events:
            logger.warning("没有可用的散步事件，将返回一个 'nothing' 事件。")
            return {"type": "nothing", "description": "「{pet_name}」散了一圈, 但什么也没发生。"}

        total_weight = sum(event.get('weight', 0) for event in self.walk_events)
        if total_weight == 0:
            return random.choice(self.walk_events)

        roll = random.uniform(0, total_weight)
        current_weight = 0
        for event in self.walk_events:
            current_weight += event.get('weight', 0)
            if roll < current_weight:
                return event
        return random.choice(self.walk_events) # 备用

    def _parse_reward_value(self, value: int | list) -> int:
        """解析奖励值，支持整数或[min, max]范围。"""
        if isinstance(value, list) and len(value) == 2:
            try:
                return random.randint(int(value[0]), int(value[1]))
            except ValueError:
                return 0
        elif isinstance(value, int):
            return value
        return 0

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
        """检查并处理宠物升级，返回一个包含升级和技能学习消息的列表。"""
        level_up_messages = []
        pet = self._get_pet(user_id, group_id)
        if not pet: return []

        pet_type_config = self.pets_data.get(pet['pet_type'])
        if not pet_type_config: return []
        learnset = pet_type_config.get('learnset', {})

        while True:
            pet = self._get_pet(user_id, group_id) # 重新获取最新数据
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

                # 检查技能学习
                moves_learned = learnset.get(str(new_level))
                if moves_learned:
                    for move in moves_learned:
                        level_up_messages.append(f"💡 你的宠物「{pet['pet_name']}」似乎可以学习新技能「{move}」了！")
                    level_up_messages.append("请使用 `/宠物技能` 查看详情，并使用 `/学习技能` 来管理技能。")

            else:
                break
        return level_up_messages

    def _generate_pet_status_image(self, pet_data: dict, sender_name: str) -> Path | str:
        """根据宠物数据生成一张状态图（已更新为显示状态和持有物）。"""
        try:
            W, H = 800, 600
            bg_path = self.assets_dir / "background.png"
            font_path = self.assets_dir / "font.ttf"
            img = Image.open(bg_path).resize((W, H))
            draw = ImageDraw.Draw(img)
            font_title = ImageFont.truetype(str(font_path), 40)
            font_text = ImageFont.truetype(str(font_path), 28)
            font_text_small = ImageFont.truetype(str(font_path), 24)

            pet_type_info = self.pets_data.get(pet_data['pet_type'])
            if not pet_type_info: return "错误：找不到该宠物的配置数据。"

            evo_info = pet_type_info['evolutions'][str(pet_data['evolution_stage'])]
            pet_img_path = self.assets_dir / evo_info['image']
            pet_img = Image.open(pet_img_path).convert("RGBA").resize((300, 300))
            img.paste(pet_img, (50, 150), pet_img)

            draw.text((W / 2, 50), f"{pet_data['pet_name']}的状态", font=font_title, fill="white", anchor="mt")
            draw.text((400, 150), f"主人: {sender_name}", font=font_text, fill="white")
            draw.text((400, 200), f"种族: {evo_info['name']} ({pet_data['pet_type']})", font=font_text, fill="white")
            draw.text((400, 250), f"等级: Lv.{pet_data['level']}", font=font_text, fill="white")

            # --- v1.5 新增：显示状态 ---
            status = pet_data.get('status_condition')
            if status:
                status_name = STAT_MAP.get(status, "未知")
                draw.text((600, 250), f"状态:【{status_name}】", font=font_text, fill="#FF6666") # 红色高亮

            exp_needed = self._exp_for_next_level(pet_data['level'])
            exp_ratio = min(1.0, pet_data['exp'] / exp_needed) if exp_needed > 0 else 1.0
            draw.text((400, 300), f"经验: {pet_data['exp']} / {exp_needed}", font=font_text, fill="white")
            draw.rectangle([400, 340, 750, 360], outline="white", fill="gray")
            draw.rectangle([400, 340, 400 + 350 * exp_ratio, 360], fill="#66ccff")

            draw.text((400, 380), f"攻击: {pet_data['attack']}", font=font_text, fill="white")
            draw.text((600, 380), f"防御: {pet_data['defense']}", font=font_text, fill="white")
            draw.text((400, 420), f"心情: {pet_data['mood']}/100", font=font_text, fill="white")
            draw.text((600, 420), f"饱食度: {pet_data['satiety']}/100", font=font_text, fill="white")

            # --- v1.5 新增：显示持有物 ---
            held_item = pet_data.get('held_item')
            held_item_name = f"持有: {held_item}" if held_item else "持有: [无]"
            draw.text((400, 460), held_item_name, font=font_text, fill="#FFFF99") # 黄色
            draw.text((400, 500), f"金钱: ${pet_data.get('money', 0)}", font=font_text, fill="#FFD700")

            # --- 显示技能 ---
            draw.text((50, 460), "--- 技能 ---", font=font_text, fill="white")
            moves = [pet_data.get('move1'), pet_data.get('move2'), pet_data.get('move3'), pet_data.get('move4')]
            y_offset = 500
            for i, move in enumerate(moves):
                move_name = move if move else "[ -- ]"
                move_attr = self.moves_data.get(move, {}).get('attribute', '普通')
                draw.text((50, y_offset + i*25), f"[{i+1}] {move_name} ({move_attr})", font=font_text_small, fill="white")

            output_path = self.cache_dir / f"status_{pet_data['group_id']}_{pet_data['user_id']}.png"
            img.save(output_path, format='PNG')
            return output_path
        except FileNotFoundError as e:
            logger.error(f"生成状态图失败，缺少素材文件: {e}")
            return f"生成状态图失败，请检查插件素材文件是否完整：{e}"
        except Exception as e:
            logger.error(f"生成状态图时发生未知错误: {e}")
            return f"生成状态图时发生未知错误: {e}"

    def _get_attribute_multiplier(self, move_attr: str, defender_attr: str) -> float:
        """计算属性克制伤害倍率 (v1.5 新增 电/毒)。"""
        # A克B: A -> B
        effectiveness = {
            "水": ["火"],
            "火": ["草"],
            "草": ["水", "电"], # 假设草克电 (地面)
            "电": ["水"],
            "毒": ["草"]
        }
        # B克A: B -> A
        resistance = {
            "水": ["火", "水"],
            "火": ["火", "草"],
            "草": ["草", "水", "电"],
            "电": ["电"],
            "毒": ["毒"]
        }

        if defender_attr in effectiveness.get(move_attr, []):
            return 1.2 # 效果拔群
        if move_attr in resistance.get(defender_attr, []):
             return 0.8 # 效果不佳

        return 1.0 # 普通

    # --- 战斗核心 (v1.5 重构) ---
    def _run_battle(self, pet1_orig: dict, pet2_orig: dict) -> tuple[list[str], str]:
        """执行两个宠物之间的对战（v1.5 重构，支持状态和持有物）。"""
        log = []

        # 深拷贝，防止战斗中的状态修改影响到原始数据
        pet1 = deepcopy(pet1_orig)
        pet2 = deepcopy(pet2_orig)

        p1_hp = pet1['level'] * 10 + 50
        p2_hp = pet2['level'] * 10 + 50
        p1_name, p2_name = pet1['pet_name'], pet2['pet_name']

        p1_pet_attr = self.pets_data[pet1['pet_type']]['attribute']
        p2_pet_attr = self.pets_data[pet2['pet_type']]['attribute']

        p1_moves = [m for m in [pet1.get('move1'), pet1.get('move2'), pet1.get('move3'), pet1.get('move4')] if m]
        p2_moves = [m for m in [pet2.get('move1'), pet2.get('move2'), pet2.get('move3'), pet2.get('move4')] if m]

        log.append(
            f"战斗开始！\n「{p1_name}」(Lv.{pet1['level']} {p1_pet_attr}系) vs 「{p2_name}」(Lv.{pet2['level']} {p2_pet_attr}系)")

        if pet1.get('held_item'): log.append(f"「{p1_name}」携带着「{pet1['held_item']}」。")
        if pet2.get('held_item'): log.append(f"「{p2_name}」携带着「{pet2['held_item']}」。")


        def calculate_turn(attacker, defender, defender_hp, attacker_moves, defender_pet_attr, turn_log):
            """计算一个回合的完整逻辑。"""

            attacker_status = attacker.get('status_condition')
            new_defender_status = defender.get('status_condition')

            # --- 1. 回合开始：检查状态 ---
            if attacker_status == 'SLEEP':
                if random.random() < 0.5: # 50% 几率醒来
                    attacker['status_condition'] = None
                    turn_log.append(f"「{attacker['pet_name']}」醒过来了！")
                else:
                    turn_log.append(f"「{attacker['pet_name']}」正在熟睡...")
                    return defender_hp, new_defender_status, turn_log

            if attacker_status == 'PARALYSIS':
                if random.random() < 0.25: # 25% 几率无法动弹
                    turn_log.append(f"「{attacker['pet_name']}」麻痹了，无法动弹！")
                    return defender_hp, new_defender_status, turn_log

            # --- 2. 选择技能 ---
            if not attacker_moves:
                chosen_move_name = "挣扎"
                move_data = {"attribute": "普通", "power": 35, "description": "拼命地挣扎。"}
            else:
                chosen_move_name = random.choice(attacker_moves)
                move_data = self.moves_data.get(chosen_move_name)
                if not move_data:
                    chosen_move_name = "挣扎"
                    move_data = {"attribute": "普通", "power": 35, "description": "拼命地挣扎。"}

            move_power = move_data.get('power', 0)
            move_attr = move_data.get('attribute', '普通')

            turn_log.append(f"「{attacker['pet_name']}」使用了「{chosen_move_name}」！")

            # --- 3. 计算伤害 (如果 power > 0) ---
            if move_power > 0:
                # --- 3a. 计算攻防 (计入状态和持有物) ---
                satiety_mod = 0.5 + (attacker['satiety'] / 100) * 0.7
                if attacker['satiety'] < 20:
                    turn_log.append(f"「{attacker['pet_name']}」饿得有气无力...")

                eff_attack = attacker['attack'] * satiety_mod
                eff_defense = defender['defense'] * (0.5 + (defender['satiety'] / 100) * 0.7)

                # 应用持有物
                if attacker.get('held_item') == "力量头带": eff_attack *= 1.1
                if defender.get('held_item') == "坚硬外壳": eff_defense *= 1.1

                # --- 3b. 计算暴击 ---
                crit_chance = 0.05 + (attacker['mood'] / 100) * 0.20
                is_crit = random.random() < crit_chance
                crit_multiplier = 1.3 + (attacker['mood'] / 100) * 0.4

                # --- 3c. 计算克制和伤害 ---
                attr_multiplier = self._get_attribute_multiplier(move_attr, defender_pet_attr)
                level_diff_mod = 1 + (attacker['level'] - defender['level']) * 0.02

                base_dmg = max(1, (eff_attack * 0.7 + move_power * 1.5) - (eff_defense * 0.6))

                final_dmg = int(base_dmg * attr_multiplier * level_diff_mod)
                if is_crit:
                    final_dmg = int(final_dmg * crit_multiplier)

                defender_hp -= final_dmg

                if is_crit: turn_log.append("💥 会心一击！")
                if attr_multiplier > 1.2:
                    turn_log.append("效果拔群！")
                elif attr_multiplier < 1.0:
                    turn_log.append("效果不太理想…")
                turn_log.append(f"对「{defender['pet_name']}」造成了 {final_dmg} 点伤害！(剩余HP: {max(0, defender_hp)})")

            # --- 4. 结算技能效果 (无论伤害如何) ---
            if move_data.get('effect') and defender.get('status_condition') is None: # 无法覆盖已有的状态
                effect_type = move_data['effect'].get('type')
                effect_chance = move_data['effect'].get('chance', 1.0)

                if random.random() < effect_chance:
                    # 检查属性免疫 (例如 电系 不会 麻痹)
                    immune = False
                    if effect_type == 'POISON' and defender_pet_attr == '毒': immune = True
                    if effect_type == 'PARALYSIS' and defender_pet_attr == '电': immune = True

                    if not immune:
                        new_defender_status = effect_type
                        defender['status_condition'] = new_defender_status # 更新字典中的状态
                        status_name = STAT_MAP.get(new_defender_status, "异常")
                        turn_log.append(f"「{defender['pet_name']}」陷入了「{status_name}」状态！")
                    else:
                        turn_log.append(f"「{defender['pet_name']}」免疫该状态！")

            return defender_hp, new_defender_status, turn_log


        turn = 0
        while p1_hp > 0 and p2_hp > 0:
            turn += 1
            log.append(f"\n--- 第 {turn} 回合 ---")

            # --- 回合开始：结算P1中毒 ---
            if pet1.get('status_condition') == 'POISON':
                poison_dmg = max(1, int(pet1['level'] * 0.5))
                p1_hp -= poison_dmg
                log.append(f"「{p1_name}」受到了 {poison_dmg} 点中毒伤害。")
                if p1_hp <= 0: break

            # --- P1 行动 ---
            turn_log_1 = []
            p2_hp, pet2['status_condition'], turn_log_1 = calculate_turn(
                pet1, pet2, p2_hp, p1_moves, p2_pet_attr, turn_log_1
            )
            log.extend(turn_log_1)
            if p2_hp <= 0: break

            # --- 回合开始：结算P2中毒 ---
            if pet2.get('status_condition') == 'POISON':
                poison_dmg = max(1, int(pet2['level'] * 0.5))
                p2_hp -= poison_dmg
                log.append(f"「{p2_name}」受到了 {poison_dmg} 点中毒伤害。")
                if p2_hp <= 0: break

            # --- P2 行动 ---
            turn_log_2 = []
            p1_hp, pet1['status_condition'], turn_log_2 = calculate_turn(
                pet2, pet1, p1_hp, p2_moves, p1_pet_attr, turn_log_2
            )
            log.extend(turn_log_2)
            if p1_hp <= 0: break

        winner_name = p1_name if p1_hp > 0 else p2_name
        log.append(f"\n战斗结束！胜利者是「{winner_name}」！")

        # --- 战斗后结算状态 ---
        with sqlite3.connect(self.db_path) as conn:
            # 睡眠状态在战斗结束后自动解除
            p1_final_status = None if pet1.get('status_condition') == 'SLEEP' else pet1.get('status_condition')
            p2_final_status = None if pet2.get('status_condition') == 'SLEEP' else pet2.get('status_condition')

            conn.execute("UPDATE pets SET status_condition = ? WHERE user_id = ? AND group_id = ?",
                         (p1_final_status, int(pet1_orig['user_id']), int(pet1_orig['group_id'])))
            conn.execute("UPDATE pets SET status_condition = ? WHERE user_id = ? AND group_id = ?",
                         (p2_final_status, int(pet2_orig['user_id']), int(pet2_orig['group_id'])))
            conn.commit()

        return log, winner_name
    # --- 战斗核心结束 ---


    @filter.command("领养宠物")
    async def adopt_pet(self, event: AstrMessageEvent, pet_name: str | None = None):
        """领养一只随机的初始宠物（已更新为使用技能系统）。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("该功能仅限群聊使用。")
            return

        if self._get_pet(user_id, group_id):
            yield event.plain_result("你在这个群里已经有一只宠物啦！发送 /我的宠物 查看。")
            return

        available_pets = list(self.pets_data.keys())
        if not available_pets:
            yield event.plain_result("错误：宠物配置文件为空，请联系管理员。")
            return

        type_name = random.choice(available_pets)
        if not pet_name: pet_name = type_name

        pet_info = self.pets_data[type_name]
        stats = pet_info['base_stats']
        now_iso = datetime.now().isoformat()

        # --- 分配初始技能 ---
        learnset = pet_info.get('learnset', {})
        default_moves = learnset.get('1', ["撞击"]) # 默认1级技能
        moves = (default_moves + [None] * 4)[:4] # 填充技能栏

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO pets (user_id, group_id, pet_name, pet_type, attack, defense, last_updated_time, move1, move2, move3, move4)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(user_id), int(group_id), pet_name, type_name, stats['attack'], stats['defense'], now_iso,
                 moves[0], moves[1], moves[2], moves[3])
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
        """带宠物散步，触发随机奇遇或PVE战斗"""
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
        exp_gain, money_gain, mood_gain, satiety_gain = 0, 0, 0, 0

        event_data = self._select_walk_event()
        event_type = event_data.get('type', 'nothing')
        description = event_data.get('description', '...').format(pet_name=pet['pet_name'])
        final_reply.append(description)

        if event_type == 'reward':
            reward_type = event_data.get('reward_type')
            reward_value = self._parse_reward_value(event_data.get('reward_value', 0))
            money_gain = self._parse_reward_value(event_data.get('money_gain', 0))

            if reward_type == 'exp':
                exp_gain = reward_value
                final_reply.append(f"你的宠物获得了 {exp_gain} 点经验值！")
            elif reward_type == 'mood':
                mood_gain = reward_value
                final_reply.append(f"你的宠物心情提升了 {mood_gain} 点！")
            elif reward_type == 'satiety':
                satiety_gain = reward_value
                final_reply.append(f"你的宠物饱食度提升了 {satiety_gain} 点！")

            if money_gain > 0:
                final_reply.append(f"意外之喜！你在路边捡到了 ${money_gain}！")

        elif event_type == 'pve':
            npc_level = max(1, pet['level'] + random.randint(-1, 1))
            npc_type_name = random.choice(list(self.pets_data.keys()))
            npc_pet_info = self.pets_data[npc_type_name]
            npc_stats = npc_pet_info['base_stats']

            # 为NPC分配技能
            npc_learnset = npc_pet_info.get('learnset', {})
            npc_available_moves = []
            for lvl_str, moves in npc_learnset.items():
                if int(lvl_str) <= npc_level:
                    npc_available_moves.extend(moves)

            if not npc_available_moves: npc_available_moves = ["撞击"]
            chosen_moves = (random.sample(npc_available_moves, min(len(npc_available_moves), 4)) + [None] * 4)[:4]

            npc_pet = {
                "user_id": "0", "group_id": "0", # 假ID
                "pet_name": f"野生的{npc_type_name}", "pet_type": npc_type_name,
                "level": npc_level, "attack": npc_stats['attack'] + npc_level,
                "defense": npc_stats['defense'] + npc_level, "satiety": 100, "mood": 100,
                "move1": chosen_moves[0], "move2": chosen_moves[1],
                "move3": chosen_moves[2], "move4": chosen_moves[3],
                "status_condition": None, "held_item": None # 野生宠物默认无状态
            }

            battle_log, winner_name = self._run_battle(pet, npc_pet)
            final_reply.extend(battle_log)

            if winner_name == pet['pet_name']:
                exp_gain = npc_level * 5 + random.randint(1, 5)
                money_gain = random.randint(5, 15)
                final_reply.append(f"\n胜利了！你获得了 {exp_gain} 点经验值和 ${money_gain} 赏金！")
            else:
                exp_gain = 1
                final_reply.append(f"\n很遗憾，你的宠物战败了，但也获得了 {exp_gain} 点经验。")

        elif event_type == 'minigame':
            if random.random() < event_data.get('win_chance', 0.5):
                # 胜利
                win_reward = event_data.get('win_reward', {})
                money_gain = self._parse_reward_value(win_reward.get('money', 0))
                mood_gain = self._parse_reward_value(win_reward.get('mood', 0))
                final_reply.append(event_data.get('win_text', '胜利了！'))
            else:
                # 失败
                final_reply.append(event_data.get('lose_text', '失败了...'))

        elif event_type == 'nothing':
            pass # 描述已在开头添加

        # --- 统一更新数据库 ---
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE pets SET 
                       exp = exp + ?, 
                       money = money + ?, 
                       mood = MIN(100, mood + ?), 
                       satiety = MIN(100, satiety + ?), 
                       last_walk_time = ? 
                       WHERE user_id = ? AND group_id = ?""",
                    (exp_gain, money_gain, mood_gain, satiety_gain, now.isoformat(), int(user_id), int(group_id))
                )
                conn.commit()
        except Exception as e:
            logger.error(f"散步事件更新数据库时出错: {e}")
            final_reply.append("（系统错误：保存奖励失败，请联系管理员）")

        # --- 检查升级 ---
        if exp_gain > 0:
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

        pet_type_info = self.pets_data.get(pet['pet_type'])
        if not pet_type_info: return

        current_evo_info = pet_type_info['evolutions'][str(pet['evolution_stage'])]

        evolve_level = current_evo_info['evolve_level']
        if not evolve_level:
            yield event.plain_result(f"「{pet['pet_name']}」已是最终形态，无法再进化。")
            return

        if pet['level'] < evolve_level:
            yield event.plain_result(f"「{pet['pet_name']}」需达到 Lv.{evolve_level} 才能进化。")
            return

        next_evo_stage = pet['evolution_stage'] + 1
        next_evo_info = pet_type_info['evolutions'].get(str(next_evo_stage))
        if not next_evo_info:
             yield event.plain_result(f"「{pet['pet_name']}」已是最终形态，无法再进化。")
             return

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

    # --- 技能管理命令 ---
    @filter.command("宠物技能")
    async def pet_moves(self, event: AstrMessageEvent):
        """查看宠物的技能学习情况。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return
        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物哦。")
            return

        pet_config = self.pets_data.get(pet['pet_type'])
        if not pet_config:
            yield event.plain_result("错误：找不到宠物配置。")
            return

        learnset = pet_config.get('learnset', {})

        reply = f"--- 「{pet['pet_name']}」的技能 ---\n"
        reply += "【当前技能】\n"
        current_moves = [pet.get('move1'), pet.get('move2'), pet.get('move3'), pet.get('move4')]
        for i, move in enumerate(current_moves):
            if move:
                move_data = self.moves_data.get(move, {})
                power = move_data.get('power', '?')
                attr = move_data.get('attribute', '?')
                reply += f"[{i+1}] {move} (威力:{power} {attr}系)\n"
            else:
                reply += f"[{i+1}] -- 空 --\n"

        reply += "\n【可学技能】(按等级)\n"
        available_moves = []
        for lvl_str, moves in learnset.items():
            if int(lvl_str) <= pet['level']:
                available_moves.extend(moves)

        if not available_moves:
            reply += "暂无可学习的技能。\n"
        else:
            # 去重并保持顺序
            seen = set()
            unique_moves = [m for m in available_moves if not (m in seen or seen.add(m))]
            reply += "、".join(unique_moves)
            reply += "\n\n使用 `/学习技能 [栏位] [技能名]` 来替换技能。"

        yield event.plain_result(reply)

    @filter.command("学习技能")
    async def learn_move(self, event: AstrMessageEvent, slot: int, move_name: str):
        """让宠物在指定栏位学习一个新技能。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物。")
            return

        if not 1 <= slot <= 4:
            yield event.plain_result("技能栏位必须是 1 到 4 之间。")
            return

        pet_config = self.pets_data.get(pet['pet_type'])
        if not pet_config:
            yield event.plain_result("错误：找不到宠物配置。")
            return

        # 检查是否在可学列表里
        learnset = pet_config.get('learnset', {})
        can_learn = False
        for lvl_str, moves in learnset.items():
            if int(lvl_str) <= pet['level'] and move_name in moves:
                can_learn = True
                break

        # 检查是否通过TM（技能光盘）学习
        is_tm = False
        if not can_learn:
            item_name = f"技能光盘-{move_name}"
            if item_name in SHOP_ITEMS and SHOP_ITEMS[item_name]['type'] == 'tm':
                # 检查背包
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT quantity FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ?",
                        (int(user_id), int(group_id), item_name)
                    )
                    item_row = cursor.fetchone()
                    if item_row and item_row[0] > 0:
                        is_tm = True
                    else:
                        yield event.plain_result(f"你的宠物等级不足，且背包中没有「{item_name}」。")
                        return
            else:
                 yield event.plain_result(f"你的宠物等级不足，无法学习「{move_name}」。")
                 return

        if move_name not in self.moves_data:
             yield event.plain_result(f"技能库中不存在名为「{move_name}」的技能。")
             return

        move_col = f"move{slot}"
        old_move = pet.get(move_col) or "空栏位"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE pets SET {move_col} = ? WHERE user_id = ? AND group_id = ?",
                (move_name, int(user_id), int(group_id))
            )

            # 如果是TM，则消耗掉
            if is_tm:
                item_name = f"技能光盘-{move_name}"
                conn.execute(
                    "UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                    (int(user_id), int(group_id), item_name)
                )
                conn.execute(
                    "DELETE FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ? AND quantity <= 0",
                    (int(user_id), int(group_id), item_name))

            conn.commit()

        learn_msg = f"学习成功！「{pet['pet_name']}」忘记了「{old_move}」，学会了「{move_name}」！"
        if is_tm:
            learn_msg += f"\n（消耗了 1 个「技能光盘-{move_name}」）"

        yield event.plain_result(learn_msg)


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

        if quantity <= 0:
            yield event.plain_result("购买数量必须大于0。")
            return

        if item_name not in SHOP_ITEMS:
            yield event.plain_result(f"商店里没有「{item_name}」这种东西。")
            return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，无法购买物品。")
            return

        item_info = SHOP_ITEMS[item_name]
        total_cost = item_info['price'] * quantity

        if pet.get('money', 0) < total_cost:
            yield event.plain_result(f"你的钱不够哦！购买 {quantity} 个「{item_name}」需要 ${total_cost}，你只有 ${pet.get('money', 0)}。")
            return

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pets SET money = money - ? WHERE user_id = ? AND group_id = ?",
                (total_cost, int(user_id), int(group_id))
            )

            cursor.execute("""
                    INSERT INTO inventory (user_id, group_id, item_name, quantity) 
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, group_id, item_name) 
                    DO UPDATE SET quantity = quantity + excluded.quantity
                """, (int(user_id), int(group_id), item_name, quantity))
            conn.commit()

        yield event.plain_result(f"购买成功！你花费 ${total_cost} 购买了 {quantity} 个「{item_name}」。")

    # --- v1.5 /投喂 -> /使用 ---
    @filter.command("使用")
    async def use_item(self, event: AstrMessageEvent, item_name: str):
        """从背包中使用物品（食物、药品等）。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物，不能使用物品哦。")
            return

        if item_name not in SHOP_ITEMS:
            yield event.plain_result(f"「{item_name}」不是一个可用的物品。")
            return

        item_info = SHOP_ITEMS[item_name]
        item_type = item_info.get('type')

        # --- 检查背包是否有此物品 ---
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ?",
                (int(user_id), int(group_id), item_name)
            )
            item_row = cursor.fetchone()

            if not item_row or item_row[0] <= 0:
                yield event.plain_result(f"你的背包里没有「{item_name}」。")
                return

            # --- 消耗物品 ---
            cursor.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                (int(user_id), int(group_id), item_name)
            )
            cursor.execute(
                "DELETE FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ? AND quantity <= 0",
                (int(user_id), int(group_id), item_name))

            reply_msg = ""

            # --- 根据物品类型处理效果 ---
            if item_type == 'food':
                satiety_gain = item_info.get('satiety', 0)
                mood_gain = item_info.get('mood', 0)
                cursor.execute(
                    "UPDATE pets SET satiety = MIN(100, satiety + ?), mood = MIN(100, mood + ?) WHERE user_id = ? AND group_id = ?",
                    (satiety_gain, mood_gain, int(user_id), int(group_id))
                )
                s_name = STAT_MAP.get('satiety')
                m_name = STAT_MAP.get('mood')
                reply_msg = f"你给「{pet['pet_name']}」投喂了「{item_name}」，它的{s_name}增加了 {satiety_gain}，{m_name}增加了 {mood_gain}！"

            elif item_type == 'status_heal':
                status_cured = item_info.get('cures')
                current_status = pet.get('status_condition')
                if current_status == status_cured:
                    cursor.execute(
                        "UPDATE pets SET status_condition = NULL WHERE user_id = ? AND group_id = ?",
                        (int(user_id), int(group_id))
                    )
                    status_name = STAT_MAP.get(status_cured, "异常")
                    reply_msg = f"你对「{pet['pet_name']}」使用了「{item_name}」，它的「{status_name}」状态被治愈了！"
                else:
                    reply_msg = f"「{item_name}」对你的宠物没有效果。"
                    # 把物品还回去
                    cursor.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                                   (int(user_id), int(group_id), item_name))

            elif item_type == 'held_item':
                reply_msg = f"「{item_name}」是持有物，请使用 `/装备 {item_name}` 来给宠物携带。"
                # 把物品还回去
                cursor.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                                (int(user_id), int(group_id), item_name))

            elif item_type == 'tm':
                reply_msg = f"「{item_name}」是技能光盘，请使用 `/学习技能 [栏位] {item_info.get('move_name')}` 来学习。"
                # 把物品还回去
                cursor.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                                (int(user_id), int(group_id), item_name))

            else:
                reply_msg = f"你使用了「{item_name}」，但似乎什么也没发生..."

            conn.commit()
            yield event.plain_result(reply_msg)

    # --- v1.5 新增：装备命令 ---
    @filter.command("装备")
    async def equip_item(self, event: AstrMessageEvent, item_name: str):
        """从背包中装备一个持有物。"""
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id: return

        pet = self._get_pet(user_id, group_id)
        if not pet:
            yield event.plain_result("你还没有宠物。")
            return

        if item_name not in SHOP_ITEMS or SHOP_ITEMS[item_name].get('type') != 'held_item':
            yield event.plain_result(f"「{item_name}」不是一个可以装备的持有物。")
            return

        # 检查背包
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ?",
                (int(user_id), int(group_id), item_name)
            )
            item_row = cursor.fetchone()

            if not item_row or item_row[0] <= 0:
                yield event.plain_result(f"你的背包里没有「{item_name}」。")
                return

            # --- 卸下旧装备 (如果有) ---
            old_item = pet.get('held_item')
            if old_item:
                cursor.execute("""
                    INSERT INTO inventory (user_id, group_id, item_name, quantity) VALUES (?, ?, ?, 1)
                    ON CONFLICT(user_id, group_id, item_name) 
                    DO UPDATE SET quantity = quantity + 1
                """, (int(user_id), int(group_id), old_item))

            # --- 消耗新装备 (从背包) ---
            cursor.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND group_id = ? AND item_name = ?",
                (int(user_id), int(group_id), item_name)
            )
            cursor.execute(
                "DELETE FROM inventory WHERE user_id = ? AND group_id = ? AND item_name = ? AND quantity <= 0",
                (int(user_id), int(group_id), item_name))

            # --- 装备到宠物 ---
            cursor.execute(
                "UPDATE pets SET held_item = ? WHERE user_id = ? AND group_id = ?",
                (item_name, int(user_id), int(group_id))
            )
            conn.commit()

        reply = f"装备成功！「{pet['pet_name']}」现在携带着「{item_name}」。"
        if old_item:
            reply += f"\n（已将「{old_item}」放回背包）"
        yield event.plain_result(reply)

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
        menu_text = """--- 🐾 宠物插件帮助菜单 v1.5 🐾 ---
【核心功能】
/领养宠物 [名字] - 领养一只新宠物。
/我的宠物 - 查看宠物详细状态图(含状态/持有物)。
/宠物改名 [新名] - 给你的宠物换个名字。
/宠物进化 - 当宠物达到等级时进化。

【技能与装备】
/宠物技能 - 查看当前技能和可学技能。
/学习技能 [栏位] [技能名] - 学习新技能。
/装备 [物品名] - 让宠物携带一个持有物。

【日常互动】
/宠物签到 - 每天领取金钱奖励。
/散步 - 带宠物散步，触发奇遇或战斗。
/使用 [物品名] - 使用食物或药品。 (原/投喂)

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
        logger.info("简易群宠物游戏插件(astrbot_plugin_pet)已卸载。")