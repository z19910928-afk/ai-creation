"""LongFormBench 合成长篇生成器。

生成一个结构完整的长篇项目并写入 SQLite，供一致性引擎做可复现基线：

- 默认 40 章正文，每章约 3000 汉字（``--chapters`` 可调）；
- 人物 / 地点 / 物品 / 组织 / 妖兽五类实体；
- canon 断言、角色状态、时间线事件、关系、伏笔与来源片段；
- 所有"干净"记录都满足 schema v1.1 的 canon 写入约束（证据 + 复核人）。

本模块只生成**没有矛盾**的干净数据；已知矛盾的注入在 ``inject.py``。
临时数据库建议放在 ``benchmarks/longform/.work/``（已加入 .gitignore）。

用法::

    .venv/bin/python benchmarks/longform/generate.py --db /tmp/longform.sqlite3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V1 = ROOT / "docs" / "schema-v1.sql"
SCHEMA_V11 = ROOT / "docs" / "schema-v1.1-migration.sql"

PROJECT_ID = "lf_project"
BRANCH_ID = "lf_branch"
ACTOR_ID = "lf_actor"
PROJECT_NAME = "雾隐山河录"
REVIEWER = "LongFormBench 复核"
REVIEWED_AT = "2026-10-20T09:00:00+08:00"
CREATED_AT = "2026-10-01T08:00:00+08:00"
DEFAULT_SEED = 20261023

# --------------------------------------------------------------------------- #
# app_* 投影表 DDL：与 backend/server.py 中 ``ensure_schema`` 的语义保持一致。
# 这里只复制一致性引擎会读取的投影表；字段顺序/约束按 server.py 的最终形态
# （含后续 ALTER TABLE 补上的列）书写。
# --------------------------------------------------------------------------- #
APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_project_meta(
  project_id TEXT PRIMARY KEY REFERENCES project(id),
  summary TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '', goal INTEGER NOT NULL DEFAULT 50000,
  book_status TEXT NOT NULL DEFAULT 'writing',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_entity_meta(
  entity_id TEXT PRIMARY KEY REFERENCES entity(id),
  description TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_chapter_meta(
  chapter_id TEXT PRIMARY KEY REFERENCES document_node(id),
  status TEXT NOT NULL DEFAULT '草稿',
  chapter_goal TEXT NOT NULL DEFAULT '', scene_plan TEXT NOT NULL DEFAULT '',
  pov TEXT NOT NULL DEFAULT '', foreshadowing_op TEXT NOT NULL DEFAULT '',
  hook TEXT NOT NULL DEFAULT '', recap TEXT NOT NULL DEFAULT '',
  next_brief TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'unreviewed',
  review_json TEXT NOT NULL DEFAULT '{}', reviewed_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_deleted_node(
  chapter_id TEXT PRIMARY KEY REFERENCES document_node(id),
  deleted_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_plot_item(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  branch_id TEXT NOT NULL REFERENCES branch(id), stage TEXT NOT NULL
    CHECK(stage IN ('setup','develop','payoff')), text TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_story_relation(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  from_entity_id TEXT NOT NULL REFERENCES entity(id), to_entity_id TEXT NOT NULL REFERENCES entity(id),
  relation_type TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS app_timeline_event(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  title TEXT NOT NULL, event_time TEXT NOT NULL DEFAULT '', chapter_id TEXT REFERENCES document_node(id),
  entity_ids_json TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
  ordinal INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS app_foreshadow_link(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  plot_id TEXT NOT NULL REFERENCES app_plot_item(id), chapter_id TEXT REFERENCES document_node(id),
  kind TEXT NOT NULL CHECK(kind IN ('plant','develop','payoff')), note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
"""

# --------------------------------------------------------------------------- #
# 词库与句式模板
# --------------------------------------------------------------------------- #
WEATHERS = ["细雨", "薄雪", "大风", "暮色", "晨雾", "月色", "雷雨", "霜气", "斜阳", "夜露"]
TIMES = ["清晨", "正午", "黄昏", "深夜", "子时", "破晓", "午后", "掌灯时分"]
SOUNDS = ["更鼓", "雁鸣", "水声", "剑鸣", "马蹄", "风声", "钟声", "橹声", "鸦啼", "笛声"]
EMOTIONS = ["沉默", "警觉", "疲惫", "迟疑", "决然", "怅然", "平静", "焦躁"]
ACTS = ["赶路", "守夜", "疗伤", "对饮", "巡山", "问诊", "练剑", "追查", "抄录", "议事"]
OBJECTS = ["信", "药包", "地图", "令牌", "账册", "残页", "名册", "玉佩", "火折子", "半枚铜钱"]
BODIES = ["手腕", "肩头", "眉心", "指尖", "后背", "颈侧"]
ADJS = ["冷", "旧", "薄", "沉", "暗", "涩", "潮", "脆"]
VERBS = ["握", "按", "收", "推", "递", "扣", "挑"]
NUMS = ["一", "两", "三", "四", "五", "六", "七", "八", "九", "十"]
DIALOGUE_LINES = [
    "山雨要来了",
    "你比从前慢",
    "这件事没有完",
    "我等你很久了",
    "东西不在我这里",
    "你信不信我",
    "路只有一条",
    "别回头",
    "他还活着",
    "账不是这么算的",
    "我要一个答案",
    "今夜就走",
    "有人动过这里",
    "你不该来",
    "把剑放下",
    "名字不重要",
    "我记得你的声音",
    "还差一味药",
    "城门外有动静",
    "先活下来再说",
]

TEMPLATES: dict[str, list[str]] = {
    "scenery": [
        "{loc}的{weather}压在檐角，{sound}从远处一层层漫过来。",
        "{time}的{loc}很安静，只有{sound}在石阶之间来回碰响。",
        "雾从{loc}的水面升起来，把屋脊和远处的灯火一起吞掉。",
        "{loc}外的{weather}没有停，{sound}被风吹得忽远忽近。",
        "天光落在{loc}的青石板上，像一层没有擦干的{adj}水痕。",
        "{c}推开门时，{loc}正笼在{weather}里，{sound}贴着屋脊滑过去。",
        "檐下的灯笼晃了两下，{loc}的影子在墙上拉长又缩短。",
        "这一带的路{adj}得厉害，{loc}的界碑被{weather}磨得只剩半个字。",
        "{sound}响起的时候，{loc}的雾正好漫过第三级台阶。",
        "{loc}的夜比别处来得早，{time}刚过，灯就一盏盏亮了。",
        "风从{loc}的方向吹来，带着{adj}的潮气。",
        "{loc}的旧墙根下积着水，映出{time}的一点天光。",
        "远处的{sound}断了一瞬，又接上，{loc}显得更空。",
        "{c}在{loc}外站了一会儿，{weather}落满肩头。",
    ],
    "action": [
        "{c}把{item}横在膝上，指腹沿着剑脊慢慢推过去。",
        "{c}收起{item}，起身往{loc}的方向走。",
        "{c}抬手拂开额前的{weather}，指节在{body}上停了一停。",
        "{c}没有回头，只把{item}往{c2}那边推了推。",
        "{c}沿着墙根走了半圈，忽然停下，侧耳去听{sound}。",
        "{c}在案前坐下，把{obj}摊开，一页一页往下看。",
        "{c}替{c2}拢了拢衣领，动作很轻，像怕惊动什么。",
        "{c}把{obj}塞进怀里，转身时衣角带起一阵{adj}的风。",
        "{c}提剑出门，脚步在{loc}的石阶上敲出很轻的回声。",
        "{c}接过{obj}，指尖在封口处按了一下，才收进袖中。",
        "{c}蹲下身，用{item}挑开地上的碎石。",
        "{c}和{c2}并肩走了很长一段，谁都没有先开口。",
        "{c}把灯芯挑亮，{loc}的阴影便往后退了一寸。",
        "{c}在门前停住，抬手敲了三下，又敲了两下。",
    ],
    "dialogue": [
        "「{line}」{c}说。",
        "「{line}」{c}低声说，目光没有离开{loc}的方向。",
        "「{line}」{c2}答得很慢，像是在掂量每一个字。",
        "「{line}」{c}笑了一下，笑意没有到眼底。",
        "「{line}」{c}把{item}放到桌上，声音很平。",
        "「{line}」{c2}问。",
        "「{line}」{c}顿了顿，又补了一句，「{line2}」",
        "「{line}」{c}说，「这件事，先不要让别人知道。」",
        "「{line}」{c2}没有接话，只把{obj}推回{c}面前。",
        "「{line}」{c}的声音低下去，被{sound}盖住了一半。",
        "「{line}」{c}说完，转身去看{loc}外的{weather}。",
        "「{line}」{c2}终于开口，语气比方才软了些。",
    ],
    "interior": [
        "{c}没有立刻回答。{emotion}像一层薄霜，落在眉间。",
        "{c}忽然想起很多年前的一个{time}，也是这样的{weather}。",
        "{c}知道自己不该再问，可那句话已经到了舌尖。",
        "{c}把{obj}捏在手里，指节一点点收紧。",
        "{c}望着{loc}的方向，心里那点{emotion}慢慢沉下去。",
        "{c}没有说出口。有些话一旦落地，就再也收不回来。",
        "{c}忽然觉得{loc}比记忆里窄了许多。",
        "{c}闭上眼，听见自己的心跳和{sound}叠在一起。",
        "{c}想起{item}第一次出鞘时的样子，那时候还没有这么多顾虑。",
        "{c}把{obj}放回原处，动作比来时慢了很多。",
        "{c}不确定自己还能信谁，但这个念头只停了一瞬。",
        "{c}在心里把接下来的路又走了一遍，仍旧没有把握。",
    ],
    "transition": [
        "这一夜，{loc}的灯一直亮到{time}。",
        "{weather}在{time}停了，{loc}只剩下滴水的声音。",
        "等{sound}再次响起，{loc}已经换了一层天色。",
        "{c}回到住处时，{time}已过，{loc}静得能听见自己的脚步。",
        "第二天{time}，{loc}的雾散了一半，路却更难走。",
        "此后三日，{loc}没有再传来消息。",
        "{c}把{obj}留在桌上，推门走进{weather}里。",
        "灯灭之后，{loc}还有一扇窗亮着。",
    ],
    "clash": [
        "{c}的{item}与{c2}的{item}撞在一起，{sound}在{loc}里炸开。",
        "{c}侧身避开，袖口被划开一道{adj}口子。",
        "{c2}退了两步，脚跟抵住{loc}的门槛。",
        "两人在{loc}里过了十几招，谁都没有占到便宜。",
        "{c}收剑时慢了半拍，{c2}已经欺到身前。",
        "{c2}抬手挡住这一击，{body}上立刻浮起一道红痕。",
        "{c}听见{item}落地的声音，心里反而静了下来。",
        "{c2}没有追，只站在原地看着{c}退进{weather}里。",
    ],
    "clue": [
        "{c}在{obj}的夹层里摸到一片薄纸，上面只有半个印。",
        "那枚印和{org}旧档里的记号对得上。",
        "{c2}提到{loc}时，{c}注意到他改了称呼。",
        "{obj}上有一道新添的划痕，位置和上次不同。",
        "{c}把{obj}对着灯看，纸背透出几个很淡的字。",
        "{loc}的账册缺了{num}页，断口很整齐。",
        "{c}在{loc}的墙角找到一小片{adj}的布。",
        "那句话和{c2}三个月前说过的，几乎一字不差。",
    ],
}

# --------------------------------------------------------------------------- #
# 人物与实体设定
# --------------------------------------------------------------------------- #
CHARACTERS: list[dict[str, Any]] = [
    {
        "name": "沈砚舟", "aliases": ["沈七", "砚舟"], "identity": "天枢阁少主",
        "org": "天枢阁", "loc": "临江城", "goal": "查清父亲失踪的真相",
        "item": "青霜剑", "trait": "克制", "age": 24,
        "appearance": "眉骨很高，左手虎口有一道旧疤", "weapon": "青霜剑",
        "secret": "他记得灭门那夜的火光，却从不对人提起", "fear": "再次看见身边的人无声无息地消失",
        "origin": "雾隐山", "catchphrase": "先把眼前的事做完", "weakness": "对旧人心软",
        "note": "主角，天枢阁少主",
    },
    {
        "name": "苏晚棠", "aliases": ["晚棠", "苏姑娘"], "identity": "药王谷医女",
        "org": "药王谷", "loc": "临江城", "goal": "找到失踪的师父",
        "item": "药王鼎", "trait": "温和", "age": 22,
        "appearance": "常穿月白衫，袖口沾着药渍", "weapon": "银针",
        "secret": "她的脉案里藏着一份毒方", "fear": "救人失败",
        "origin": "云梦泽", "catchphrase": "先别动", "weakness": "见伤者便忘了自己",
        "note": "女主，药王谷医女",
    },
    {
        "name": "陆青崖", "aliases": ["陆统领"], "identity": "玄衣卫统领",
        "org": "玄衣卫", "loc": "落雁关", "goal": "肃清玄衣卫内部的内鬼",
        "item": "玄铁令", "trait": "冷硬", "age": 31,
        "appearance": "左颊有一道刀疤", "weapon": "长刀",
        "secret": "他放走过一个该杀的人", "fear": "军令与良心相悖",
        "origin": "青石镇", "catchphrase": "按规矩办", "weakness": "护短",
        "note": "亦敌亦友的玄衣卫统领",
    },
    {
        "name": "裴照雪", "aliases": ["裴楼主"], "identity": "听雪楼楼主",
        "org": "听雪楼", "loc": "临江城", "goal": "守住听雪楼的情报网",
        "item": "寒玉佩", "trait": "从容", "age": 28,
        "appearance": "眼尾微挑，说话总带三分笑", "weapon": "折扇",
        "secret": "她与玄衣卫做过一笔交易", "fear": "失去听雪楼",
        "origin": "临江城", "catchphrase": "消息比刀快", "weakness": "护着柳含烟",
        "note": "听雪楼楼主",
    },
    {
        "name": "顾长风", "aliases": ["顾舵主"], "identity": "沧海盟舵主",
        "org": "沧海盟", "loc": "落霞渡", "goal": "重振沧海盟",
        "item": "星盘", "trait": "豪爽", "age": 38,
        "appearance": "身形魁梧，右臂有旧伤", "weapon": "双刀",
        "secret": "沧海盟当年是被他亲手解散的", "fear": "旧部不再信他",
        "origin": "北冥海", "catchphrase": "先喝一碗", "weakness": "耳根软",
        "note": "沧海盟舵主",
    },
    {
        "name": "白露", "aliases": ["露儿"], "identity": "沈砚舟的侍女",
        "org": "天枢阁", "loc": "临江城", "goal": "替沈砚舟守住退路",
        "item": "青铜面具", "trait": "沉默", "age": 19,
        "appearance": "总低着头，手指细长", "weapon": "短匕",
        "secret": "她是被安插进来的眼线", "fear": "身份被发现",
        "origin": "青石镇", "catchphrase": "公子", "weakness": "心软",
        "note": "沈砚舟的侍女",
    },
    {
        "name": "韩九霄", "aliases": ["韩长老"], "identity": "天枢阁长老",
        "org": "天枢阁", "loc": "雾隐山", "goal": "稳住天枢阁的局势",
        "item": "《山河策》残卷", "trait": "多疑", "age": 56,
        "appearance": "须发半白，右手常拢在袖中", "weapon": "铁杖",
        "secret": "他知道沈家灭门的另一半真相", "fear": "天枢阁毁在自己手里",
        "origin": "雾隐山", "catchphrase": "不可", "weakness": "疑心太重",
        "note": "天枢阁长老",
    },
    {
        "name": "温如玉", "aliases": ["温谷主"], "identity": "药王谷谷主",
        "org": "药王谷", "loc": "云梦泽", "goal": "保住药王谷的清净",
        "item": "赤焰珠", "trait": "寡言", "age": 47,
        "appearance": "面容清瘦，指尖常年沾着药香", "weapon": "药杵",
        "secret": "她当年救过一个不该救的人", "fear": "谷中秘方外流",
        "origin": "云梦泽", "catchphrase": "药医不死人", "weakness": "护短",
        "note": "药王谷谷主",
    },
    {
        "name": "谢无咎", "aliases": ["老谢"], "identity": "玄衣卫暗桩",
        "org": "玄衣卫", "loc": "青石镇", "goal": "摆脱玄衣卫的控制",
        "item": "玄铁令", "trait": "谨慎", "age": 35,
        "appearance": "脸上总带着一道浅浅的笑纹", "weapon": "暗弩",
        "secret": "他的家人还活着", "fear": "身份暴露",
        "origin": "临江城", "catchphrase": "我只是路过", "weakness": "挂念家人",
        "note": "玄衣卫安插在青石镇的暗桩",
    },
    {
        "name": "柳含烟", "aliases": ["含烟"], "identity": "临江城花魁",
        "org": "听雪楼", "loc": "临江城", "goal": "攒够钱离开临江城",
        "item": "寒玉佩", "trait": "机敏", "age": 26,
        "appearance": "眼尾有一点朱砂痣", "weapon": "簪子",
        "secret": "她记得每一个来听曲的客人", "fear": "被人赎走",
        "origin": "临江城", "catchphrase": "客人说笑了", "weakness": "念旧",
        "note": "听雪楼的花魁，也是情报中间人",
    },
    {
        "name": "秦慕白", "aliases": ["秦将军"], "identity": "落雁关守将",
        "org": "玄衣卫", "loc": "落雁关", "goal": "守住落雁关",
        "item": "玄铁令", "trait": "刚直", "age": 33,
        "appearance": "甲胄不离身", "weapon": "长枪",
        "secret": "他早知朝廷不会增援", "fear": "城破",
        "origin": "落雁关", "catchphrase": "城在人在", "weakness": "不懂变通",
        "note": "落雁关守将",
    },
    {
        "name": "阿箬", "aliases": ["箬姑娘"], "identity": "苗疆蛊师",
        "org": "药王谷", "loc": "云梦泽", "goal": "找到解蛊的最后一味药",
        "item": "赤焰珠", "trait": "直率", "age": 21,
        "appearance": "银饰满身，赤脚", "weapon": "蛊虫",
        "secret": "她身上种着母蛊", "fear": "母蛊失控",
        "origin": "云梦泽", "catchphrase": "有毒", "weakness": "不谙世事",
        "note": "药王谷客卿，苗疆蛊师",
    },
]

LOCATIONS: list[dict[str, str]] = [
    {"name": "雾隐山", "region": "北境", "terrain": "终年云雾不散的山脉", "faction": "天枢阁", "rumor": "山里埋着前朝的最后一座书阁"},
    {"name": "临江城", "region": "江南", "terrain": "水网密布的江城", "faction": "听雪楼", "rumor": "夜里能听见江底的钟声"},
    {"name": "落雁关", "region": "北境", "terrain": "两山之间的关隘", "faction": "玄衣卫", "rumor": "关外的雁从不往南飞"},
    {"name": "断魂谷", "region": "西境", "terrain": "寸草不生的裂谷", "faction": "沧海盟", "rumor": "谷底的雾会记住每一个死人"},
    {"name": "云梦泽", "region": "南境", "terrain": "常年积水的泽地", "faction": "药王谷", "rumor": "泽心有会发光的药草"},
    {"name": "青石镇", "region": "江南", "terrain": "一条主街的小镇", "faction": "玄衣卫", "rumor": "镇口的老槐树下埋着名单"},
    {"name": "北冥海", "region": "极北", "terrain": "终年结冰的海面", "faction": "沧海盟", "rumor": "冰层下有会唱歌的巨兽"},
    {"name": "落霞渡", "region": "西境", "terrain": "黄河边的旧渡口", "faction": "沧海盟", "rumor": "渡船只在黄昏开"},
]

ITEMS: list[dict[str, str]] = [
    {"name": "青霜剑", "holder": "沈砚舟", "power": "剑身遇血会结霜", "origin": "雾隐山剑冢", "status": "完整"},
    {"name": "玄铁令", "holder": "陆青崖", "power": "可调动玄衣卫三城暗桩", "origin": "玄衣卫大营", "status": "完整"},
    {"name": "《山河策》残卷", "holder": "韩九霄", "power": "记载前朝龙脉与兵力部署", "origin": "前朝书阁", "status": "残缺三页"},
    {"name": "赤焰珠", "holder": "温如玉", "power": "能压制寒毒，也会反噬持有者", "origin": "北冥海冰层下", "status": "封存"},
    {"name": "寒玉佩", "holder": "裴照雪", "power": "贴身佩戴可辨毒", "origin": "听雪楼旧藏", "status": "完整"},
    {"name": "青铜面具", "holder": "白露", "power": "戴上后声音会被扭曲", "origin": "断魂谷", "status": "有裂纹"},
    {"name": "星盘", "holder": "顾长风", "power": "能推算潮汐与航线", "origin": "沧海盟祖传", "status": "缺一角"},
    {"name": "药王鼎", "holder": "苏晚棠", "power": "炼药时不会炸炉", "origin": "药王谷", "status": "完整"},
]

ORGS: list[dict[str, str]] = [
    {"name": "天枢阁", "leader": "韩九霄", "base": "雾隐山", "tenet": "记录真相，不替任何人隐瞒"},
    {"name": "玄衣卫", "leader": "陆青崖", "base": "落雁关", "tenet": "只认军令，不认人情"},
    {"name": "药王谷", "leader": "温如玉", "base": "云梦泽", "tenet": "药能救人，也能杀人"},
    {"name": "听雪楼", "leader": "裴照雪", "base": "临江城", "tenet": "消息可以卖，但不能假"},
    {"name": "沧海盟", "leader": "顾长风", "base": "落霞渡", "tenet": "盟约一散，旧部仍在"},
]

CREATURES: list[dict[str, str]] = [
    {"name": "雪隼", "habitat": "雾隐山", "danger": "低", "note": "天枢阁用来传信的白隼"},
    {"name": "墨蛟", "habitat": "北冥海", "danger": "极高", "note": "冰层下的巨兽，只在风暴夜现身"},
]

ARCHIVED_ENTITY = {"name": "断刃", "kind": "物品", "description": "一把断掉的旧刀，曾属于沈家旧部"}

CHAPTER_TITLE_PHRASES = [
    "雾起临江", "剑鸣雪夜", "故人来信", "断桥残雪", "药谷春深", "玄衣夜行",
    "山雨欲来", "旧案重提", "孤城闻笛", "灯下无影", "北冥有蛟", "落雁归时",
    "一纸山河", "听雪楼主", "青石旧约", "云梦迷津", "星盘初动", "寒玉生烟",
    "长夜将尽", "雾散之时",
]

# 干净关系（from, to, type）：全部唯一、双向均存在且 active。
CLEAN_RELATIONS: list[tuple[str, str, str]] = [
    ("沈砚舟", "苏晚棠", "挚友"),
    ("沈砚舟", "白露", "主仆"),
    ("沈砚舟", "韩九霄", "师徒"),
    ("沈砚舟", "陆青崖", "宿敌"),
    ("沈砚舟", "天枢阁", "归属"),
    ("沈砚舟", "青霜剑", "持有"),
    ("沈砚舟", "雾隐山", "试炼"),
    ("苏晚棠", "温如玉", "师徒"),
    ("苏晚棠", "药王谷", "归属"),
    ("苏晚棠", "药王鼎", "持有"),
    ("苏晚棠", "临江城", "行医"),
    ("陆青崖", "谢无咎", "上下级"),
    ("陆青崖", "玄衣卫", "归属"),
    ("陆青崖", "玄铁令", "持有"),
    ("陆青崖", "裴照雪", "同门"),
    ("裴照雪", "柳含烟", "旧识"),
    ("裴照雪", "听雪楼", "执掌"),
    ("裴照雪", "寒玉佩", "持有"),
    ("顾长风", "秦慕白", "盟友"),
    ("顾长风", "沧海盟", "归属"),
    ("顾长风", "星盘", "持有"),
    ("顾长风", "落霞渡", "盘踞"),
    ("白露", "沈砚舟", "侍奉"),
    ("白露", "青铜面具", "持有"),
    ("韩九霄", "温如玉", "故交"),
    ("韩九霄", "天枢阁", "长老"),
    ("韩九霄", "《山河策》残卷", "保管"),
    ("温如玉", "药王谷", "谷主"),
    ("温如玉", "赤焰珠", "保管"),
    ("谢无咎", "玄衣卫", "暗桩"),
    ("谢无咎", "青石镇", "潜伏"),
    ("柳含烟", "临江城", "栖身"),
    ("柳含烟", "听雪楼", "挂名"),
    ("秦慕白", "落雁关", "镇守"),
    ("阿箬", "苏晚棠", "同门"),
    ("阿箬", "云梦泽", "来自"),
    ("阿箬", "赤焰珠", "觊觎"),
]

# 干净角色状态（entity, attribute, value, valid_start）
CHARACTER_STATE_VALUES = {
    "武力": ["二流", "一流", "绝顶", "二流", "一流", "三流", "绝顶", "一流", "二流", "三流", "一流", "二流"],
    "伤势": ["左肩旧伤", "无", "右臂刀伤未愈", "无", "内息不稳", "无", "旧疾", "无", "轻伤", "无", "箭伤初愈", "无"],
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _cn_num(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    return str(value)


def create_database(path: str | Path) -> sqlite3.Connection:
    """按 schema v1 + v1.1 迁移 + app_* 投影建库，返回连接。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_V1.read_text(encoding="utf-8"))
    conn.executescript(SCHEMA_V11.read_text(encoding="utf-8"))
    conn.executescript(APP_SCHEMA)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# 正文生成
# --------------------------------------------------------------------------- #
class ProseWriter:
    """确定性的模板化中文长文生成器（只服务于字数量级与来源片段）。"""

    def __init__(self, rng: random.Random, characters: Iterable[str], locations: Iterable[str],
                 items: Iterable[str], orgs: Iterable[str]) -> None:
        self.rng = rng
        self.pools: dict[str, list[str]] = {
            "c": list(characters),
            "c2": list(characters),
            "loc": list(locations),
            "item": list(items),
            "org": list(orgs),
            "weather": WEATHERS,
            "time": TIMES,
            "sound": SOUNDS,
            "emotion": EMOTIONS,
            "act": ACTS,
            "obj": OBJECTS,
            "body": BODIES,
            "adj": ADJS,
            "verb": VERBS,
            "num": NUMS,
            "line": DIALOGUE_LINES,
            "line2": DIALOGUE_LINES,
        }

    def sentence(self, bank: str) -> str:
        template = self.rng.choice(TEMPLATES[bank])
        values = {key: self.rng.choice(pool) for key, pool in self.pools.items()}
        if values["c"] == values["c2"]:
            values["c2"] = self.rng.choice([name for name in self.pools["c"] if name != values["c"]])
        return template.format(**values)

    def paragraph(self, *, allow_clash: bool) -> list[str]:
        banks = ["action", "dialogue", "interior", "scenery", "clue"]
        weights = [3, 3, 2, 1, 1]
        if allow_clash:
            banks.append("clash")
            weights.append(1)
        count = self.rng.randint(3, 5)
        return [self.sentence(self.rng.choices(banks, weights=weights, k=1)[0]) for _ in range(count)]

    def scene(self, *, allow_clash: bool) -> list[list[str]]:
        paragraphs = [[self.sentence("scenery")]]
        paragraphs.extend(self.paragraph(allow_clash=allow_clash) for _ in range(6))
        paragraphs.append([self.sentence("transition")])
        return paragraphs

    def chapter(self, ordinal: int, title: str) -> tuple[str, list[tuple[str, int, int]]]:
        paragraphs: list[list[str]] = []
        for scene_index in range(5):
            paragraphs.extend(self.scene(allow_clash=scene_index in {2, 4}))
        text = title + "\n\n" + "\n\n".join("".join(paragraph) for paragraph in paragraphs)
        sentences = [sentence for paragraph in paragraphs for sentence in paragraph]
        quotes: list[tuple[str, int, int]] = []
        for index in (len(sentences) // 4, len(sentences) * 3 // 4):
            sentence = sentences[index]
            if not (10 <= len(sentence) <= 80):
                sentence = next((item for item in sentences if 10 <= len(item) <= 80), sentence)
            start = text.index(sentence)
            quotes.append((sentence, start, start + len(sentence)))
        return text, quotes


# --------------------------------------------------------------------------- #
# 插入辅助
# --------------------------------------------------------------------------- #
def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    assertion_id: str,
    entity_id: str,
    predicate: str,
    obj: Any,
    evidence: list[dict[str, Any]],
    epistemic: str = "world_truth",
    state: str = "canon",
    valid_start: str | None = None,
    valid_end: str | None = None,
    supersedes_id: str | None = None,
    reviewed_by: str | None = REVIEWER,
    reviewed_at: str | None = REVIEWED_AT,
) -> None:
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,"
        "reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            assertion_id,
            PROJECT_ID,
            BRANCH_ID,
            entity_id,
            predicate,
            json.dumps(obj, ensure_ascii=False, sort_keys=True),
            epistemic,
            state,
            valid_start,
            valid_end,
            0.95 if state == "canon" else 0.6,
            supersedes_id,
            json.dumps(evidence, ensure_ascii=False),
            reviewed_by if state == "canon" else None,
            reviewed_at if state == "canon" else None,
        ),
    )


def _insert_timeline_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    title: str,
    event_time: str,
    chapter_id: str | None,
    ordinal: int,
    status: str = "planned",
    summary: str = "",
) -> None:
    conn.execute(
        "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,"
        "entity_ids_json,summary,status,ordinal,created_at,updated_at,evidence_json) "
        "VALUES(?,?,?,?,?,?,?,'',?,?,?,?,'{}')",
        (
            event_id, PROJECT_ID, BRANCH_ID, title, event_time, chapter_id,
            "[]", status, ordinal, CREATED_AT, CREATED_AT,
        ),
    )


def _insert_plot_item(conn: sqlite3.Connection, *, plot_id: str, stage: str, text: str, archived: int = 0) -> None:
    conn.execute(
        "INSERT INTO app_plot_item(id,project_id,branch_id,stage,text,created_at,updated_at,archived) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (plot_id, PROJECT_ID, BRANCH_ID, stage, text, CREATED_AT, CREATED_AT, archived),
    )


def _insert_foreshadow_link(conn: sqlite3.Connection, *, link_id: str, plot_id: str,
                            chapter_id: str | None, kind: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO app_foreshadow_link(id,project_id,branch_id,plot_id,chapter_id,kind,note,"
        "created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,'{}')",
        (link_id, PROJECT_ID, BRANCH_ID, plot_id, chapter_id, kind, note, CREATED_AT, CREATED_AT),
    )


def _insert_relation(conn: sqlite3.Connection, *, relation_id: str, source_id: str,
                     target_id: str, relation_type: str, status: str = "active", note: str = "") -> None:
    conn.execute(
        "INSERT INTO app_story_relation(id,project_id,branch_id,from_entity_id,to_entity_id,"
        "relation_type,note,status,created_at,updated_at,evidence_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,'{}')",
        (relation_id, PROJECT_ID, BRANCH_ID, source_id, target_id, relation_type, note,
         status, CREATED_AT, CREATED_AT),
    )


# --------------------------------------------------------------------------- #
# 主生成流程
# --------------------------------------------------------------------------- #
def generate(conn: sqlite3.Connection, *, chapters: int = 40, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """生成干净项目，返回数据集统计与 hard-negative 清单。"""
    if chapters < 1:
        raise ValueError("chapters 必须 >= 1")
    rng = random.Random(seed)

    # ---- 项目 / 分支 / 作者 ----
    conn.execute(
        "INSERT INTO project(id,schema_version,name,created_at) VALUES(?,?,?,?)",
        (PROJECT_ID, 11, PROJECT_NAME, CREATED_AT),
    )
    conn.execute(
        "INSERT INTO branch(id,project_id,parent_id,merge_base_revision,head_revision,status,acl_json) "
        "VALUES(?,?,NULL,NULL,NULL,'active','{}')",
        (BRANCH_ID, PROJECT_ID),
    )
    conn.execute(
        "INSERT INTO actor(id,project_id,kind,name) VALUES(?,?,?,?)",
        (ACTOR_ID, PROJECT_ID, "user", "LongFormBench"),
    )
    conn.execute(
        "INSERT INTO app_project_meta(project_id,summary,genre,goal,book_status,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (PROJECT_ID, "一部关于旧案、盟约与身份的长篇合成小说。", "武侠/悬疑", chapters * 3000,
         "writing", CREATED_AT),
    )

    character_names = [item["name"] for item in CHARACTERS]
    location_names = [item["name"] for item in LOCATIONS]
    item_names = [item["name"] for item in ITEMS]
    org_names = [item["name"] for item in ORGS]
    writer = ProseWriter(rng, character_names, location_names, item_names, org_names)

    # ---- 章节、正文、来源片段 ----
    chapter_ids: list[str] = []
    chapter_spans: dict[int, list[str]] = {}
    chapter_texts: list[str] = []
    quote_records: dict[int, list[tuple[str, int, int]]] = {}
    for ordinal in range(1, chapters + 1):
        title = f"第{_cn_num(ordinal)}章 {CHAPTER_TITLE_PHRASES[(ordinal - 1) % len(CHAPTER_TITLE_PHRASES)]}"
        text, quotes = writer.chapter(ordinal, title)
        chapter_texts.append(text)
        quote_records[ordinal] = quotes
        node_id = f"ch{ordinal:03d}"
        revision_id = f"rev{ordinal:03d}"
        chapter_ids.append(node_id)
        conn.execute(
            "INSERT INTO document_node(id,project_id,branch_id,parent_id,kind,title,ordinal) "
            "VALUES(?,?,?,NULL,'chapter',?,?)",
            (node_id, PROJECT_ID, BRANCH_ID, title, ordinal),
        )
        conn.execute(
            "INSERT INTO document_revision(id,document_node_id,parent_id,branch_id,content_hash,"
            "canonical_text,created_at,actor_id,operation) VALUES(?,?,NULL,?,?,?,?,?,'create')",
            (revision_id, node_id, BRANCH_ID, _hash(text), text.encode("utf-8"), CREATED_AT, ACTOR_ID),
        )
        spans: list[str] = []
        for quote_index, (quote, start, end) in enumerate(quotes):
            span_id = f"span{ordinal:03d}{'ab'[quote_index]}"
            spans.append(span_id)
            conn.execute(
                "INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,"
                "end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,'valid')",
                (
                    span_id, PROJECT_ID, revision_id, node_id, start, end,
                    len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8")),
                    _hash(quote), quote,
                ),
            )
        chapter_spans[ordinal] = spans
        conn.execute(
            "INSERT INTO app_chapter_meta(chapter_id,status,chapter_goal,pov,updated_at) "
            "VALUES(?,?,?,?,?)",
            (node_id, "定稿", f"推进第 {ordinal} 个情节点", CHARACTERS[(ordinal - 1) % len(CHARACTERS)]["name"],
             CREATED_AT),
        )

    # ---- 实体 ----
    entity_ids: dict[str, str] = {}
    entity_rows: list[tuple[str, str, str, list[str], str, str]] = []
    for index, item in enumerate(CHARACTERS, 1):
        entity_rows.append((f"ent_c{index:02d}", "人物", item["name"], item["aliases"], "active", item["note"]))
    for index, item in enumerate(LOCATIONS, 1):
        entity_rows.append((f"ent_l{index:02d}", "地点", item["name"], [], "active", item["rumor"]))
    for index, item in enumerate(ITEMS, 1):
        entity_rows.append((f"ent_i{index:02d}", "物品", item["name"], [], "active", item["power"]))
    for index, item in enumerate(ORGS, 1):
        entity_rows.append((f"ent_o{index:02d}", "组织", item["name"], [], "active", item["tenet"]))
    for index, item in enumerate(CREATURES, 1):
        entity_rows.append((f"ent_x{index:02d}", "妖兽", item["name"], [], "active", item["note"]))
    entity_rows.append(("ent_i09", "物品", ARCHIVED_ENTITY["name"], [], "archived", ARCHIVED_ENTITY["description"]))

    for entity_id, kind, name, aliases, status, description in entity_rows:
        conn.execute(
            "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,lineage_parent_id,status) "
            "VALUES(?,?,?,?,?,NULL,?)",
            (entity_id, PROJECT_ID, kind, name, json.dumps(aliases, ensure_ascii=False), status),
        )
        conn.execute(
            "INSERT INTO app_entity_meta(entity_id,description,updated_at) VALUES(?,?,?)",
            (entity_id, description, CREATED_AT),
        )
        entity_ids[name] = entity_id

    # ---- 干净断言 ----
    clean_assertions: list[tuple[str, str, Any]] = []

    for character in CHARACTERS:
        name = character["name"]
        clean_assertions.extend([
            (name, "has_story_fact", {"name": name, "type": "人物", "note": character["note"]}),
            (name, "身份", {"value": character["identity"]}),
            (name, "所属", {"value": character["org"]}),
            (name, "所在地", {"value": character["loc"]}),
            (name, "目标", {"value": character["goal"]}),
            (name, "持有", {"value": character["item"]}),
            (name, "性格", {"value": character["trait"]}),
            (name, "年龄", {"value": character["age"]}),
            (name, "外貌", {"value": character["appearance"]}),
            (name, "武器", {"value": character["weapon"]}),
            (name, "秘密", {"value": character["secret"]}),
            (name, "恐惧", {"value": character["fear"]}),
            (name, "出身", {"value": character["origin"]}),
            (name, "口头禅", {"value": character["catchphrase"]}),
            (name, "弱点", {"value": character["weakness"]}),
        ])
    for location in LOCATIONS:
        name = location["name"]
        clean_assertions.extend([
            (name, "has_story_fact", {"name": name, "type": "地点", "note": location["rumor"]}),
            (name, "地域", {"value": location["region"]}),
            (name, "地貌", {"value": location["terrain"]}),
            (name, "势力", {"value": location["faction"]}),
            (name, "传闻", {"value": location["rumor"]}),
        ])
    for item in ITEMS:
        name = item["name"]
        clean_assertions.extend([
            (name, "has_story_fact", {"name": name, "type": "物品", "note": item["power"]}),
            (name, "持有者", {"value": item["holder"]}),
            (name, "能力", {"value": item["power"]}),
            (name, "来历", {"value": item["origin"]}),
            (name, "状态", {"value": item["status"]}),
        ])
    for org in ORGS:
        name = org["name"]
        clean_assertions.extend([
            (name, "has_story_fact", {"name": name, "type": "组织", "note": org["tenet"]}),
            (name, "首领", {"value": org["leader"]}),
            (name, "驻地", {"value": org["base"]}),
            (name, "宗旨", {"value": org["tenet"]}),
        ])
    clean_assertions.extend([
        ("雪隼", "has_story_fact", {"name": "雪隼", "type": "妖兽", "note": CREATURES[0]["note"]}),
        ("雪隼", "栖息地", {"value": CREATURES[0]["habitat"]}),
        ("雪隼", "危险等级", {"value": CREATURES[0]["danger"]}),
        # 墨蛟故意不写 has_story_fact：留给 inject.py 的"同一实体两个名字"注入。
        ("墨蛟", "栖息地", {"value": CREATURES[1]["habitat"]}),
        ("墨蛟", "危险等级", {"value": CREATURES[1]["danger"]}),
    ])

    # 干净断言按 1..chapters-1 章轮流绑定来源片段；最后一章留给"旧 revision"注入，
    # 避免整章断言被误判为来源过期。
    evidence_chapters = max(1, chapters - 1)
    for index, (entity_name, predicate, obj) in enumerate(clean_assertions):
        chapter_ordinal = index % evidence_chapters + 1
        span_id = chapter_spans[chapter_ordinal][index % 2]
        _insert_assertion(
            conn,
            assertion_id=f"as_{index + 1:04d}",
            entity_id=entity_ids[entity_name],
            predicate=predicate,
            obj=obj,
            evidence=[{"source_span_id": span_id}],
        )

    # ---- 干净角色状态 ----
    state_index = 0
    for char_index, character in enumerate(CHARACTERS):
        name = character["name"]
        for attribute, value in (
            ("年龄", character["age"]),
            ("武力", CHARACTER_STATE_VALUES["武力"][char_index]),
            ("伤势", CHARACTER_STATE_VALUES["伤势"][char_index]),
        ):
            state_index += 1
            conn.execute(
                "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,"
                "event_id,valid_start,valid_end,knowledge_scope_json,source_assertion_id) "
                "VALUES(?,?,?,?,?,?,NULL,'2026-01-01',NULL,'{}',NULL)",
                (f"cst_{state_index:03d}", PROJECT_ID, BRANCH_ID, entity_ids[name], attribute,
                 json.dumps({"value": value}, ensure_ascii=False)),
            )

    # ---- 干净时间线事件（每章一个） ----
    seasons = ["春", "夏", "秋", "冬"]
    for ordinal in range(1, chapters + 1):
        year = 3 + (ordinal - 1) // 4
        season = seasons[(ordinal - 1) % 4]
        _insert_timeline_event(
            conn,
            event_id=f"evt_{ordinal:03d}",
            title=f"第{_cn_num(ordinal)}章事件",
            event_time=f"承平{_cn_num(year)}年{season}",
            chapter_id=chapter_ids[ordinal - 1],
            ordinal=ordinal,
            summary=f"第 {ordinal} 章的主要事件",
        )
    for index, (title, event_time) in enumerate([
        ("沈家旧案", "二十年前 冬"), ("天枢阁立誓", "十九年前 春"),
        ("玄衣卫整编", "十七年前 秋"), ("药王谷闭谷", "十五年前 夏"),
        ("沧海盟解散", "十二年前 冬"), ("落雁关之战", "九年前 秋"),
        ("听雪楼易主", "六年前 春"), ("北冥海异动", "三年前 夏"),
    ], 1):
        _insert_timeline_event(
            conn,
            event_id=f"evt_b{index:02d}",
            title=title,
            event_time=event_time,
            chapter_id=None,
            ordinal=0,
            summary="背景事件",
        )

    # ---- 干净关系 ----
    for index, (source, target, relation_type) in enumerate(CLEAN_RELATIONS, 1):
        _insert_relation(
            conn,
            relation_id=f"rel_{index:03d}",
            source_id=entity_ids[source],
            target_id=entity_ids[target],
            relation_type=relation_type,
        )

    # ---- 干净伏笔（每章一个：第 i 章埋，第 i+6 章收） ----
    for ordinal in range(1, chapters + 1):
        plot_id = f"plot_{ordinal:03d}"
        _insert_plot_item(
            conn,
            plot_id=plot_id,
            stage="payoff",
            text=f"第{_cn_num(ordinal)}章的伏笔：{CHAPTER_TITLE_PHRASES[(ordinal - 1) % len(CHAPTER_TITLE_PHRASES)]}",
        )
        _insert_foreshadow_link(
            conn, link_id=f"fsl_{ordinal:03d}a", plot_id=plot_id,
            chapter_id=chapter_ids[ordinal - 1], kind="plant", note="埋设",
        )
        payoff_ordinal = min(ordinal + 6, chapters)
        _insert_foreshadow_link(
            conn, link_id=f"fsl_{ordinal:03d}b", plot_id=plot_id,
            chapter_id=chapter_ids[payoff_ordinal - 1], kind="payoff", note="回收",
        )

    # ---- 硬负例：结构上"像矛盾"但按设计不应报出的干净记录 ----
    hard_negatives: list[dict[str, str]] = []

    def _span(ordinal: int, index: int = 0) -> str:
        """取第 ordinal 章（越界则夹到首/末章）的第 index 个来源片段。"""
        bounded = min(max(1, ordinal), chapters)
        spans = chapter_spans[bounded]
        return spans[index % len(spans)]

    def _chapter(ordinal: int) -> str:
        return chapter_ids[min(max(1, ordinal), chapters) - 1]

    # 1) 同一谓词的两个取值，但有效期不重叠（合理演变）。
    _insert_assertion(
        conn, assertion_id="as_hn01_old", entity_id=entity_ids["沈砚舟"], predicate="行踪",
        obj={"value": "临江城"}, evidence=[{"source_span_id": _span(1)}],
        valid_start="2025-01-01", valid_end="2025-06-01",
    )
    _insert_assertion(
        conn, assertion_id="as_hn01_new", entity_id=entity_ids["沈砚舟"], predicate="行踪",
        obj={"value": "落雁关"}, evidence=[{"source_span_id": _span(2)}],
        valid_start="2025-07-01", valid_end=None,
    )
    hard_negatives.append({"id": "hn-fact-01", "description": "同一谓词两个取值但有效期不重叠"})

    # 2) 重叠有效期但显式 supersedes（旧断言已被取代）。
    _insert_assertion(
        conn, assertion_id="as_hn02_old", entity_id=entity_ids["沈砚舟"], predicate="随身之物",
        obj={"value": "青霜剑"}, evidence=[{"source_span_id": _span(3)}],
    )
    _insert_assertion(
        conn, assertion_id="as_hn02_new", entity_id=entity_ids["沈砚舟"], predicate="随身之物",
        obj={"value": "玄铁令"}, evidence=[{"source_span_id": _span(4)}],
        supersedes_id="as_hn02_old",
    )
    hard_negatives.append({"id": "hn-fact-02", "description": "重叠有效期但 supersedes 链明确取代"})

    # 3) author_plan 与 world_truth 并存（作者计划不是 canon 事实）。
    _insert_assertion(
        conn, assertion_id="as_hn03_truth", entity_id=entity_ids["沈砚舟"], predicate="结局",
        obj={"value": "归隐雾隐山"}, evidence=[{"source_span_id": _span(5)}],
    )
    _insert_assertion(
        conn, assertion_id="as_hn03_plan", entity_id=entity_ids["沈砚舟"], predicate="结局",
        obj={"value": "战死落雁关"}, evidence=[{"source_span_id": _span(5, 1)}],
        epistemic="author_plan",
    )
    hard_negatives.append({"id": "hn-fact-03", "description": "author_plan 与 world_truth 并存"})

    # 4) 角色状态变化，但有效期不重叠。
    conn.execute(
        "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,event_id,"
        "valid_start,valid_end,knowledge_scope_json,source_assertion_id) "
        "VALUES('cst_hn01a',?,?,?,?,?,NULL,'2025-01-01','2025-05-01','{}',NULL)",
        (PROJECT_ID, BRANCH_ID, entity_ids["苏晚棠"], "所在地", json.dumps({"value": "云梦泽"}, ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,event_id,"
        "valid_start,valid_end,knowledge_scope_json,source_assertion_id) "
        "VALUES('cst_hn01b',?,?,?,?,?,NULL,'2025-06-01',NULL,'{}',NULL)",
        (PROJECT_ID, BRANCH_ID, entity_ids["苏晚棠"], "所在地", json.dumps({"value": "临江城"}, ensure_ascii=False)),
    )
    hard_negatives.append({"id": "hn-attr-01", "description": "角色状态变化但有效期不重叠"})

    # 5) 同一属性、同一取值、有效期重叠（重复记录但不是矛盾）。
    for suffix in ("a", "b"):
        conn.execute(
            "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,event_id,"
            "valid_start,valid_end,knowledge_scope_json,source_assertion_id) "
            "VALUES(?,?,?,?,?,?,NULL,'2026-01-01',NULL,'{}',NULL)",
            (f"cst_hn02{suffix}", PROJECT_ID, BRANCH_ID, entity_ids["陆青崖"], "称号",
             json.dumps({"value": "陆统领"}, ensure_ascii=False)),
        )
    hard_negatives.append({"id": "hn-attr-02", "description": "同一属性同一取值重叠记录"})

    # 6) 同一对人物重复关系，但其中一条已 inactive。
    _insert_relation(
        conn, relation_id="rel_hn01", source_id=entity_ids["陆青崖"], target_id=entity_ids["裴照雪"],
        relation_type="同门", status="inactive", note="旧同门记录",
    )
    hard_negatives.append({"id": "hn-relation-01", "description": "重复关系但一条已 inactive"})

    # 7) 已归档伏笔只有 payoff：归档项不参与检查。
    _insert_plot_item(conn, plot_id="plot_hn01", stage="develop", text="已废弃的旧伏笔", archived=1)
    _insert_foreshadow_link(conn, link_id="fsl_hn01a", plot_id="plot_hn01",
                            chapter_id=_chapter(3), kind="payoff", note="旧回收")
    hard_negatives.append({"id": "hn-foreshadow-01", "description": "归档伏笔只有回收记录"})

    # 8) develop 伏笔尚未回收，但埋设时间距当前不足 15 章。
    _insert_plot_item(conn, plot_id="plot_hn02", stage="develop", text="最近埋下的新伏笔")
    _insert_foreshadow_link(conn, link_id="fsl_hn02a", plot_id="plot_hn02",
                            chapter_id=_chapter(max(1, chapters - 5)), kind="plant", note="最近埋设")
    hard_negatives.append({"id": "hn-foreshadow-02", "description": "develop 伏笔未回收但未超过 15 章"})

    # 9) 证据条目不是 source_span（作者口述），不应被判为来源失效。
    _insert_assertion(
        conn, assertion_id="as_hn04", entity_id=entity_ids["苏晚棠"], predicate="备注",
        obj={"value": "作者口述的补充设定"}, evidence=[{"note": "作者口述"}],
    )
    hard_negatives.append({"id": "hn-evidence-01", "description": "非 source_span 的证据条目"})

    # 10) 同一章节两个事件但故事时间相同：不算时间线断裂。
    hn_ordinal = min(20, chapters)
    hn_time = f"承平{_cn_num(3 + (hn_ordinal - 1) // 4)}年{seasons[(hn_ordinal - 1) % 4]}"
    _insert_timeline_event(
        conn, event_id="evt_hn01a", title="同章补记一", event_time=hn_time,
        chapter_id=_chapter(hn_ordinal), ordinal=220, summary="补记",
    )
    _insert_timeline_event(
        conn, event_id="evt_hn01b", title="同章补记二", event_time=hn_time,
        chapter_id=_chapter(hn_ordinal), ordinal=220, summary="补记",
    )
    hard_negatives.append({"id": "hn-timeline-01", "description": "同一章节两个事件但故事时间相同"})

    conn.commit()

    han_chars = sum(len([ch for ch in text if "\u4e00" <= ch <= "\u9fff"]) for text in chapter_texts)
    total_chars = sum(len(text) for text in chapter_texts)
    non_whitespace = sum(len("".join(text.split())) for text in chapter_texts)

    stats = {
        "project": PROJECT_NAME,
        "seed": seed,
        "chapters": chapters,
        "chapter_word_counts": {
            "han_chars": han_chars,
            "total_chars": total_chars,
            "non_whitespace_chars": non_whitespace,
            "min_han_per_chapter": min(
                len([ch for ch in text if "\u4e00" <= ch <= "\u9fff"]) for text in chapter_texts
            ),
            "max_han_per_chapter": max(
                len([ch for ch in text if "\u4e00" <= ch <= "\u9fff"]) for text in chapter_texts
            ),
            "avg_han_per_chapter": round(han_chars / chapters, 1),
        },
        "counts": {
            "entities": len(entity_rows),
            "assertions": conn.execute("SELECT COUNT(*) FROM assertion WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "character_states": conn.execute("SELECT COUNT(*) FROM character_state WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "timeline_events": conn.execute("SELECT COUNT(*) FROM app_timeline_event WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "relations": conn.execute("SELECT COUNT(*) FROM app_story_relation WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "plot_items": conn.execute("SELECT COUNT(*) FROM app_plot_item WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "foreshadow_links": conn.execute("SELECT COUNT(*) FROM app_foreshadow_link WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "source_spans": conn.execute("SELECT COUNT(*) FROM source_span WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
            "document_revisions": conn.execute("SELECT COUNT(*) FROM document_revision").fetchone()[0],
        },
        "hard_negatives": hard_negatives,
    }
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 LongFormBench 合成长篇项目")
    parser.add_argument("--db", required=True, help="输出 SQLite 路径")
    parser.add_argument("--chapters", type=int, default=40, help="章节数（默认 40）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    conn = create_database(args.db)
    try:
        stats = generate(conn, chapters=args.chapters, seed=args.seed)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
