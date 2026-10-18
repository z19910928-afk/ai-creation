#!/usr/bin/env python3
"""Offline boundary checks for the shared prose-output contract."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.moyu_output import (  # noqa: E402
    count_characters,
    find_planning_contamination,
    output_contract,
    output_prompt_constraints,
    validate_output,
)


class OutputContractTests(unittest.TestCase):
    def test_non_whitespace_count_includes_punctuation(self) -> None:
        self.assertEqual(count_characters("甲， 乙。\n\t"), 4)
        self.assertEqual(count_characters("甲， A1。\n\t"), 5)
        self.assertEqual(count_characters(""), 0)

    def test_explicit_continue_range_uses_exact_bounds_and_midpoint(self) -> None:
        contract = output_contract("continue", "续写600至850字。保持林岚视角。")
        self.assertEqual(contract["minimum_characters"], 600)
        self.assertEqual(contract["maximum_characters"], 850)
        self.assertEqual(contract["target_characters"], 725)
        self.assertEqual(contract["source"], "explicit_absolute")
        self.assertEqual(contract["counting"],
                         "non_whitespace_characters_including_punctuation")

    def test_absolute_range_variants_and_one_sided_limits(self) -> None:
        self.assertEqual(
            output_contract("polish", "润色全文，控制在 1,000-1,200 字。")[
                "minimum_characters"
            ],
            1000,
        )
        self.assertEqual(
            output_contract("continue", "正文至少600字。")["minimum_characters"],
            600,
        )
        self.assertEqual(
            output_contract("continue", "正文最多850字。")["maximum_characters"],
            850,
        )

    def test_chinese_number_ranges_and_hanzi_units_enforce_real_boundaries(self) -> None:
        for instruction in (
            "续写六百到八百五十字。",
            "续写600至八百五十个汉字。",
            "字数控制在六百至850个汉字。",
            "字数控制在600至850个汉字。",
            "续写六百至八百五十中文字。",
            "续写６００至８５０字符。",
        ):
            with self.subTest(instruction=instruction):
                contract = output_contract("continue", instruction)
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                                 (600, 850))
                self.assertEqual(contract["target_characters"], 725)
                self.assertFalse(validate_output("continue", "铁", contract)["eligible"])
                for characters in (600, 850):
                    self.assertTrue(validate_output("continue", "甲" * characters, contract)["eligible"])
                for characters in (599, 851):
                    self.assertFalse(validate_output("continue", "甲" * characters, contract)["eligible"])

    def test_chinese_integer_units_and_positional_digits_are_unambiguous(self) -> None:
        for numeral, expected in (
            ("十", 10), ("百", 100), ("千", 1000), ("万", 10_000),
            ("百万", 1_000_000), ("千万", 10_000_000),
            ("十一", 11), ("两百", 200), ("一百零二", 102),
            ("一千〇三十", 1030), ("一万", 10_000), ("两万", 20_000),
            ("一万零六百", 10_600), ("一万两千三百四十五", 12_345),
            ("十万", 100_000), ("二〇二六", 2026), ("〇一〇", 10),
        ):
            with self.subTest(numeral=numeral):
                contract = output_contract("continue", f"续写{numeral}字。")
                self.assertEqual(contract["minimum_characters"], expected)
                self.assertEqual(contract["maximum_characters"], expected)

    def test_imperative_lengths_with_have_are_enforced(self) -> None:
        for instruction in (
            "续写，正文要有六百至八百五十字。",
            "续写，必须有600至850字。",
            "正文需要有六百至850字。",
            "润色，候选正文应有600至八百五十字。",
            "续写，输出字数须有600至850字。",
        ):
            with self.subTest(instruction=instruction):
                task = "polish" if "润色" in instruction else "continue"
                contract = output_contract(task, instruction)
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                                 (600, 850))
                self.assertFalse(validate_output(task, "铁", contract)["eligible"])
                for size in (600, 850):
                    self.assertTrue(validate_output(task, "甲" * size, contract)["eligible"])
                for size in (599, 851):
                    self.assertFalse(validate_output(task, "甲" * size, contract)["eligible"])
        contract = output_contract("continue", "续写，正文要有六百字。")
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (600, 600))

    def test_imperative_story_lengths_are_not_output_requirements(self) -> None:
        for instruction in (
            "续写，碑文要有六百至八百五十字。",
            "续写，信中必须有600至850字。",
            "续写，保留要有六百字的碑文。",
            "续写，纸条上写着“正文要有600至850字”。",
        ):
            with self.subTest(instruction=instruction):
                contract = output_contract("continue", instruction)
                self.assertEqual(contract["source"], "unspecified")

    def test_quoted_output_bounds_are_enforced_without_changing_author_text(self) -> None:
        for instruction in (
            "字数控制在“600至850字”。",
            "正文要有「六百到八百五十字」。",
            '续写，字数要求为"600至八百五十个汉字"。',
            "正文长度范围：『六百至八百五十字』。",
        ):
            with self.subTest(instruction=instruction):
                original = instruction.encode("utf-8")
                contract = output_contract("continue", instruction)
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                                 (600, 850))
                self.assertFalse(validate_output("continue", "铁", contract)["eligible"])
                for size in (600, 850):
                    self.assertTrue(validate_output("continue", "甲" * size, contract)["eligible"])
                for size in (599, 851):
                    self.assertFalse(validate_output("continue", "甲" * size, contract)["eligible"])
                self.assertEqual(instruction.encode("utf-8"), original)
        self.assertEqual(
            output_contract("continue", "字数至少“六百字”。")["minimum_characters"], 600)
        self.assertEqual(
            output_contract("continue", "正文最多‘八百五十字’。")["maximum_characters"], 850)
        contract = output_contract(
            "polish", "正文长度保持在“原文的百分之八十至百分之一百三十”。",
            source_characters=100, source_scope="selection")
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (80, 130))
        self.assertEqual(contract["source_scope"], "selection")
        with self.assertRaises(ValueError):
            output_contract("continue", "字数控制在“六百百至八百五十字”。")

    def test_quoted_story_bounds_and_dialogue_do_not_become_output_requirements(self) -> None:
        for instruction in (
            "她说：“字数控制在600至850字。”",
            "她说：字数控制在“600至850字”。",
            "纸条上写着：字数控制在“600至850字”。",
            "续写，保留碑文中的‘600至850字’。",
            "续写，保留纸条上的「续写六百到八百五十字」。",
            "续写，字数控制在“她说至少600字，然后离开”。",
        ):
            with self.subTest(instruction=instruction):
                contract = output_contract("continue", instruction)
                self.assertEqual(contract["source"], "unspecified")
                self.assertIsNone(contract["minimum_characters"])
                self.assertIsNone(contract["maximum_characters"])
        contract = output_contract(
            "continue", "她说：“字数至少1000字。”字数控制在“600至850字”。")
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (600, 850))

    def test_chinese_one_sided_requirements_and_soft_targets_keep_same_gate(self) -> None:
        self.assertEqual(output_contract("continue", "正文至少六百字。")["minimum_characters"], 600)
        self.assertEqual(output_contract("continue", "正文最多八百五十汉字。")["maximum_characters"], 850)
        contract = output_contract("continue", "续写六百至八百五十字。目标约七百二十字。")
        self.assertEqual(contract["target_characters"], 720)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (600, 850))
        for instruction in ("目标约七百二十字。", "正文长度目标约七百二十个汉字。"):
            self.assertEqual(output_contract("continue", instruction)["source"], "unspecified")
        with self.assertRaisesRegex(ValueError, "字数范围"):
            output_contract("continue", "续写六百至八百五十字。目标约八百五十一字。")

    def test_chinese_percentage_ranges_use_exact_selected_source(self) -> None:
        for instruction in (
            "润色所选正文，字数保持原文的百分之八十到百分之一百三十。",
            "润色所选正文，字数保持原文的八十%至一百三十%。",
            "润色所选正文，字数保持原文的80％至百分之一百三十。",
            "润色所选正文，字数保持原文的百分之八十至130%。",
        ):
            with self.subTest(instruction=instruction):
                contract = output_contract(
                    "polish", instruction, source_text="甲" * 100, source_scope="selection")
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (80, 130))
                self.assertEqual(contract["source"], "explicit_relative")
                self.assertEqual(contract["source_characters"], 100)
                self.assertEqual(contract["source_scope"], "selection")
                self.assertFalse(validate_output("polish", "甲" * 79, contract)["eligible"])
                self.assertTrue(validate_output("polish", "甲" * 130, contract)["eligible"])
                self.assertFalse(validate_output("polish", "甲" * 131, contract)["eligible"])

    def test_chinese_percentage_one_sided_and_missing_source_are_guarded(self) -> None:
        for instruction in (
            "字数保持原文的至少百分之八十。",
            "字数不少于原文的百分之八十。",
        ):
            self.assertEqual(
                output_contract("polish", instruction, source_characters=100)["minimum_characters"], 80)
        for instruction in (
            "字数保持原文的不超过百分之一百三十。",
            "字数不超过原文的百分之一百三十。",
        ):
            self.assertEqual(
                output_contract("polish", instruction, source_characters=100)["maximum_characters"], 130)
        contract = output_contract("polish", "字数保持原文的百分之八十至百分之一百三十。")
        self.assertEqual(contract["source"], "explicit_relative_unresolved")
        self.assertFalse(validate_output("polish", "甲", contract)["eligible"])

    def test_invalid_numerals_do_not_silently_remove_or_change_length_gate(self) -> None:
        for numeral in (
            "六百百", "八十五十", "一百二", "一百零", "一千零零二",
            "一千零二百", "一万万", "万六百", "一万六百", "六百8十",
            "1,23", "600.5", "六百点五", "-600", "负六百", "两十", "零", "〇",
        ):
            with self.subTest(numeral=numeral), self.assertRaises(ValueError):
                output_contract("continue", f"续写{numeral}字。")
        for instruction in (
            "续写八百五十到六百字。",
            "字数保持原文的百分之一百三十至百分之八十。",
            "字数保持原文的百分之八十%至百分之一百三十。",
        ):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                output_contract("polish" if "百分之" in instruction else "continue",
                                instruction, source_characters=100)

    def test_chinese_story_quantities_and_quoted_targets_are_not_length_rules(self) -> None:
        for instruction in (
            "续写仓库场景：二十二点十分前离开，保留两把锁和第三次敲门。",
            "续写：她在六百米外等了三分钟。",
            "续写：碑文有六百字。",
            "续写：碑文有六百百字。",
            "续写，保留六百字的碑文。",
            "润色全文，保留原文的百分之八十至百分之一百三十的意象。",
            "润色全文，保留原文的百分之六百百至百分之一百三十的意象。",
            "她说：“正文至少六百字。”",
            "续写，保留纸条上的「续写六百到八百五十字」。",
            "润色她的话：“字数保持原文的百分之八十至百分之一百三十”。",
        ):
            with self.subTest(instruction=instruction):
                contract = output_contract("polish" if "润色" in instruction else "continue",
                                           instruction, source_characters=100)
                self.assertEqual(contract["source"], "unspecified")
                self.assertIsNone(contract["minimum_characters"])
                self.assertIsNone(contract["maximum_characters"])
        instruction = "她说：“目标约八百五十一字。”续写六百至八百五十字。目标约七百二十字。"
        contract = output_contract("continue", instruction)
        self.assertEqual(contract["target_characters"], 720)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (600, 850))
        contract = output_contract(
            "continue", "续写：碑文有五百到九百字。正文长度控制在六百至八百五十字。")
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (600, 850))
        contract = output_contract(
            "polish", "保留原文的百分之六百百至百分之一百三十的意象。"
            "字数保持原文的百分之八十至百分之一百三十。", source_characters=100)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (80, 130))

    def test_explicit_soft_target_preserves_absolute_range(self) -> None:
        for phrase, target in (
            ("目标约600字", 600),
            ("目标约720字", 720),
            ("正文长度目标：大约720字符", 720),
            ("字数目标为850字", 850),
        ):
            with self.subTest(phrase=phrase):
                contract = output_contract("continue", f"续写600至850字。{phrase}。")
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                                 (600, 850))
                self.assertEqual(contract["target_characters"], target)
                self.assertEqual(contract["source"], "explicit_absolute")
                prompt = output_prompt_constraints("continue", contract)
                self.assertIn(f"目标约{target}字", prompt)
                self.assertIn("允许600至850字", prompt)
        contract = output_contract("continue", "目标约720字；续写600至850字。")
        self.assertEqual(contract["target_characters"], 720)

    def test_soft_target_outside_range_and_conflicting_targets_are_rejected(self) -> None:
        for target in (0, 599, 851):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "字数范围"):
                output_contract("continue", f"续写600至850字。目标约{target}字。")
        with self.assertRaisesRegex(ValueError, "目标不一致"):
            output_contract("continue", "续写600至850字。目标约720字；目标约730字。")

    def test_soft_target_is_not_a_new_hard_acceptance_limit(self) -> None:
        contract = output_contract("continue", "续写600至850字。目标约720字。")
        for size in (600, 720, 850):
            with self.subTest(size=size):
                self.assertTrue(validate_output("continue", "甲" * size, contract)["eligible"])
        for size in (599, 851):
            with self.subTest(size=size):
                self.assertFalse(validate_output("continue", "甲" * size, contract)["eligible"])

    def test_short_bounded_continue_keeps_author_events_and_original_length_gate(self) -> None:
        contract = output_contract("continue", "续写600至850字。目标约720字。")
        prompt = output_prompt_constraints("continue", contract)
        self.assertIn("关键行动或选择", prompt)
        self.assertIn("保留作者指定情节与因果", prompt)
        self.assertNotIn("收束完整场景", prompt)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                         (600, 850))
        self.assertEqual(contract["target_characters"], 720)
        candidate = "甲" * 965
        result = validate_output("continue", candidate, contract)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["characters"], 965)
        self.assertEqual(candidate, "甲" * 965)

    def test_short_fragment_guidance_is_scoped_to_bounded_continuation(self) -> None:
        for task, instruction, source in (
            ("continue", "续写1000至1500字。", None),
            ("continue", "正文最多850字。", None),
            ("continue", "正文至少600字。", None),
            ("continue", "续写当前场景。", None),
            ("polish", "润色全文，字数保持原文的80%至130%。", "甲" * 600),
        ):
            with self.subTest(task=task, instruction=instruction):
                contract = output_contract(task, instruction, source_text=source)
                self.assertNotIn("关键行动或选择", output_prompt_constraints(task, contract))

    def test_quantities_distances_years_and_story_text_are_not_soft_targets(self) -> None:
        for phrase in (
            "目标约720米",
            "目标约2026年",
            "目标约720次",
            "目标约720字的碑文要保留",
            "她说：“目标约720字。”",
            "字数是碑文内容，目标约720米",
        ):
            with self.subTest(phrase=phrase):
                contract = output_contract("continue", "续写600至850字。" + phrase + "。")
                self.assertEqual(contract["target_characters"], 725)
                self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                                 (600, 850))

    def test_soft_target_alone_does_not_invent_a_range(self) -> None:
        for instruction in ("目标约720字。", "正文长度目标约720字。"):
            with self.subTest(instruction=instruction):
                contract = output_contract("continue", instruction)
                self.assertIsNone(contract["minimum_characters"])
                self.assertIsNone(contract["maximum_characters"])
                self.assertIsNone(contract["target_characters"])
                self.assertEqual(contract["source"], "unspecified")

    def test_one_sided_contracts_do_not_infer_bounds_from_soft_target(self) -> None:
        upper = output_contract("continue", "正文最多850字。目标约720字。")
        self.assertIsNone(upper["minimum_characters"])
        self.assertEqual(upper["maximum_characters"], 850)
        self.assertEqual(upper["target_characters"], 850)
        prompt = output_prompt_constraints("continue", upper)
        self.assertIn("正文最多850字", prompt)
        self.assertNotIn("目标约720", prompt)
        self.assertIn("上限前收束完整场景", prompt)
        lower = output_contract("continue", "正文至少600字。目标约720字。")
        self.assertEqual(lower["minimum_characters"], 600)
        self.assertIsNone(lower["maximum_characters"])
        self.assertEqual(lower["target_characters"], 600)

    def test_relative_polish_uses_request_scope_not_outer_full_chapter(self) -> None:
        selected = "甲，乙。\n丙丁"
        contract = output_contract(
            "polish",
            "润色下面选中的正文，字数保持原文的80%至130%。",
            source_text=selected,
            source_scope="selection",
        )
        # 6 non-whitespace characters, not the length of a surrounding chapter.
        self.assertEqual(contract["source_characters"], 6)
        self.assertEqual(contract["minimum_characters"], 5)
        self.assertEqual(contract["maximum_characters"], 7)
        self.assertEqual(contract["source"], "explicit_relative")
        self.assertEqual(contract["source_scope"], "selection")

    def test_relative_polish_can_use_a_verified_count_without_source_text(self) -> None:
        contract = output_contract(
            "polish",
            "润色下面全文，长度可在原文的80%至130%。",
            source_characters=100,
            source_scope="chapter",
        )
        self.assertEqual(contract["minimum_characters"], 80)
        self.assertEqual(contract["maximum_characters"], 130)

    def test_relative_soft_target_uses_verified_request_range(self) -> None:
        contract = output_contract(
            "polish", "润色所选正文，字数保持原文的80%至130%。目标约720字。",
            source_characters=600, source_scope="selection",
        )
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]),
                         (480, 780))
        self.assertEqual(contract["target_characters"], 720)
        self.assertEqual(contract["source_characters"], 600)
        self.assertEqual(contract["source_scope"], "selection")
        self.assertEqual(contract["source"], "explicit_relative")
        prompt = output_prompt_constraints("polish", contract)
        self.assertIn("目标约720字，允许480至780字", prompt)
        self.assertIn("完整保留事件、事实与收束", prompt)
        with self.assertRaisesRegex(ValueError, "字数范围"):
            output_contract(
                "polish", "润色所选正文，字数保持原文的80%至130%。目标约720字。",
                source_characters=100, source_scope="selection",
            )

    def test_unresolved_and_one_sided_relative_contracts_do_not_adopt_soft_target(self) -> None:
        unresolved = output_contract(
            "polish", "字数保持原文的80%至130%。目标约720字。"
        )
        self.assertEqual(unresolved["source"], "explicit_relative_unresolved")
        self.assertIsNone(unresolved["target_characters"])
        self.assertNotIn("目标约720", output_prompt_constraints("polish", unresolved))
        one_sided = output_contract(
            "polish", "字数保持原文的不超过120%。目标约100字。",
            source_characters=100,
        )
        self.assertIsNone(one_sided["minimum_characters"])
        self.assertEqual(one_sided["maximum_characters"], 120)
        self.assertEqual(one_sided["target_characters"], 120)

    def test_relative_requirement_without_request_range_stays_unresolved(self) -> None:
        contract = output_contract(
            "polish", "润色下面选中的正文，字数可在原文的80%至130%。",
            source_scope="selection",
        )
        self.assertIsNone(contract["minimum_characters"])
        self.assertIsNone(contract["maximum_characters"])
        self.assertEqual(contract["source"], "explicit_relative_unresolved")
        result = validate_output("polish", "甲", contract)
        self.assertFalse(result["eligible"])
        self.assertIn("length_base_missing", {item["code"] for item in result["issues"]})

    def test_empty_chapter_allows_absolute_continue_without_inventing_relative_range(self) -> None:
        contract = output_contract(
            "continue", "续写4至5字。", source_text="", source_scope="chapter"
        )
        self.assertEqual(contract["source_characters"], 0)
        self.assertIn("4至5", output_prompt_constraints("continue", contract))
        self.assertTrue(validate_output("continue", "甲，乙。", contract)["eligible"])
        relative = output_contract(
            "polish", "字数可在原文的80%至130%。", source_text=" \n\t"
        )
        self.assertEqual(relative["source"], "explicit_relative_unresolved")
        self.assertFalse(validate_output("polish", "甲，乙。", relative)["eligible"])

    def test_bare_percentages_in_story_material_are_not_length_requirements(self) -> None:
        contract = output_contract(
            "polish", "保留原文的80%至130%的意象，再补一段雨景。"
        )
        self.assertEqual(contract["source"], "unspecified")
        self.assertIsNone(contract["minimum_characters"])
        contract = output_contract(
            "polish", "润色下面全文，保留原文的80%至130%的意象。"
        )
        self.assertEqual(contract["source"], "unspecified")
        self.assertIsNone(contract["minimum_characters"])

    def test_no_explicit_length_does_not_invent_a_contract(self) -> None:
        for task in ("continue", "polish", "scene", "summary"):
            contract = output_contract(task, "保持克制，保留关键动作和人物视角。")
            self.assertIsNone(contract["minimum_characters"])
            self.assertIsNone(contract["maximum_characters"])
            self.assertEqual(contract["source"], "unspecified")
            result = validate_output(task, "正文。", contract)
            self.assertTrue(result["eligible"])

    def test_numbers_in_story_instructions_are_not_length_requirements(self) -> None:
        instruction = "续写仓库场景：22点10分前离开，保留两把锁和第三次敲门。"
        contract = output_contract("continue", instruction)
        self.assertEqual(contract["source"], "unspecified")
        self.assertIsNone(contract["minimum_characters"])

    def test_exact_length_is_only_taken_when_tied_to_body_requirement(self) -> None:
        self.assertEqual(
            output_contract("continue", "续写约600字。")["target_characters"], 600
        )
        # A date/quantity in prose is not a length instruction.
        self.assertEqual(
            output_contract("continue", "续写：她在600米外等了三分钟。")["source"],
            "unspecified",
        )

    def test_validate_checks_punctuation_and_whitespace_boundaries(self) -> None:
        contract = output_contract("continue", "续写4至5字。")
        good = validate_output("continue", "甲，乙。", contract)
        self.assertTrue(good["eligible"])
        self.assertEqual(good["characters"], 4)
        too_short = validate_output("continue", "甲。", contract)
        self.assertFalse(too_short["eligible"])
        self.assertIn("length_out_of_range", {item["code"] for item in too_short["issues"]})
        too_long = validate_output("continue", "甲，乙。丙丁", contract)
        self.assertFalse(too_long["eligible"])

    def test_empty_and_protocol_issues_are_mechanical_only(self) -> None:
        contract = output_contract("continue", "")
        result = validate_output("continue", "", contract, protocol_complete=False)
        self.assertFalse(result["eligible"])
        self.assertEqual(
            {"protocol_incomplete", "empty_output"},
            {item["code"] for item in result["issues"]},
        )
        self.assertEqual(result["hard_fact_status"], "requires_independent_review")
        self.assertIn("不证明事实", result["scope"])

    def test_planning_markers_are_high_confidence_and_line_anchored(self) -> None:
        for text in (
            "<think>\n先安排动作。\n</think>\n甲乙",
            "创作思路：\n先让人物离开。\n甲乙",
            "创作思路\n先让人物离开。\n甲乙",
            "我们需要回答这个问题。\n甲乙",
            "We need to write a scene.\n甲乙",
            "Let me understand the task. I'm continuing a Chinese mystery novel.\n甲乙",
            "Let me plan the scene:\n- 21:56, water is rising.\n甲乙",
            "Let me think about the request.\n甲乙",
            "Wait, I need to check the word count.\n甲乙",
        ):
            finding = find_planning_contamination("continue", text)
            self.assertIsNotNone(finding)
            self.assertEqual(finding["code"], "planning_contamination")

    def test_dialogue_and_mid_prose_meta_words_are_not_overfiltered(self) -> None:
        ordinary = (
            "她说：“我们需要回答这个问题，然后回家。”\n"
            "他把门关上，心里想起创作思路：先别惊动邻居。"
        )
        self.assertIsNone(find_planning_contamination("continue", ordinary))
        mid_line = "雨停后，我们需要回答彼此是否还要见面。"
        self.assertIsNone(find_planning_contamination("continue", mid_line))
        self.assertIsNone(find_planning_contamination(
            "polish", "他说：“Analysis: 先把灯关掉。”"
        ))
        self.assertIsNone(find_planning_contamination(
            "continue", "她把写有<think>的卡片塞进抽屉。"
        ))
        self.assertIsNone(find_planning_contamination(
            "continue", "“<think>是卡片上的印字。”她说。"
        ))
        self.assertIsNone(find_planning_contamination(
            "continue", "她说：“Let me understand the task.”然后把门推开。"
        ))
        self.assertIsNone(find_planning_contamination(
            "continue", "她低声说，Let me understand the old map, then folded it."
        ))

    def test_v5_writing_self_talk_and_explicit_body_plans_are_detected(self) -> None:
        for task in ("continue", "polish"):
            for prefix in (
                "我们需要分析任务。用户说要续写当前章节。",
                "我们得精心写一段，保持紧张。",
                "让我构思情节：",
                "让我构思一个约750字的续写：",
                "我们要续写，推进具体冲突。",
                "我们需要分析本次写作任务。",
                "我要构造候选正文，随后检查字数。",
                "下面我将输出润色后的正文：",
            ):
                with self.subTest(task=task, prefix=prefix):
                    text = prefix + "\n门后的铁链声停了。林岚按住左靴里的钥匙。"
                    finding = find_planning_contamination(task, text)
                    self.assertIsNotNone(finding)
                    self.assertEqual(finding["code"], "planning_contamination")
                    self.assertTrue(prefix.startswith(finding["quote"]))
                    result = validate_output(task, text)
                    self.assertFalse(result["eligible"])
                    self.assertEqual(result["characters"], count_characters(text))

    def test_v5_marker_phrases_in_quoted_dialogue_are_not_overfiltered(self) -> None:
        for prefix in ("我们需要分析任务。", "我们得精心写一段，保持紧张。", "让我构思情节："):
            for opening, closing in (("“", "”"), ("「", "」"), ('"', '"'), ("'", "'")):
                with self.subTest(prefix=prefix, quote=opening):
                    text = opening + prefix + closing + "她照着纸上的句子读。"
                    self.assertIsNone(find_planning_contamination("continue", text))
            self.assertIsNone(find_planning_contamination(
                "continue", "她说：“" + prefix + "”随后把纸推到窗边。"
            ))

    def test_normal_novel_monologues_with_similar_words_are_not_overfiltered(self) -> None:
        for text in (
            "我们需要分析脚印。海水已冲走最外侧的半枚鞋印。",
            "我们需要分析任务分配留下的痕迹，才能找到失踪的人。",
            "我们得精心写一段证词，父亲才能洗清冤屈。",
            "让我构思情节的是死者桌上那只没洗的杯子。",
            "让我想清楚她为什么没有回来，再把这扇门关上。",
            "林岚盯着纸上那句话：让我构思情节。她认出了字迹。",
        ):
            with self.subTest(text=text):
                self.assertIsNone(find_planning_contamination("continue", text))
                self.assertTrue(validate_output("polish", text)["eligible"])

    def test_non_body_tasks_do_not_flag_authorial_words_as_planning(self) -> None:
        self.assertIsNone(find_planning_contamination(
            "summary", "分析：人物在雨停后离开。"
        ))
        self.assertIsNone(find_planning_contamination(
            "check", "我们需要分析任务。当前人物的动机证据不足。"
        ))

    def test_prompt_constraints_preserve_contract_and_do_not_rewrite_author_text(self) -> None:
        contract = output_contract("continue", "续写600至850字。")
        prompt = output_prompt_constraints("continue", contract)
        self.assertIn("725", prompt)
        self.assertIn("600至850", prompt)
        self.assertIn("去除空白后的字符数", prompt)
        self.assertIn("标点、数字和字母均计入", prompt)
        self.assertIn("按场景需要分配各段篇幅", prompt)
        self.assertIn("不输出段落预算或检查过程", prompt)
        self.assertIn("把目标字数当作收束点而不是最低要求", prompt)
        self.assertIn("最后一句必须完整", prompt)
        self.assertIn("只返回正文", prompt)
        self.assertIn("不要解释", prompt)
        self.assertNotIn("作者补充要求", prompt)

    def test_prompt_uses_stored_target_and_preserves_original_bytes(self) -> None:
        instruction = "续写600至850字。目标约720字。\n保持林岚视角。"
        source = "21点56分。\n林岚摸到左靴内侧的月牙钥匙。"
        candidate = "她按住锁，等铁链声停下。\n" * 100
        original_bytes = tuple(value.encode("utf-8") for value in (instruction, source, candidate))
        contract = output_contract("continue", instruction, source_text=source)
        contract["target_characters"] = 700
        contract_bytes = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
        prompt = output_prompt_constraints("continue", contract)
        self.assertIn("目标约700字", prompt)
        self.assertNotIn("目标约725字", prompt)
        result = validate_output("continue", candidate, contract, protocol_complete=True)
        self.assertFalse(result["eligible"])
        self.assertEqual(tuple(value.encode("utf-8") for value in (instruction, source, candidate)),
                         original_bytes)
        self.assertEqual(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                         contract_bytes)
        legacy = {"minimum_characters": 600, "maximum_characters": 850}
        self.assertIn("目标约725字", output_prompt_constraints("continue", legacy))
        self.assertEqual(legacy, {"minimum_characters": 600, "maximum_characters": 850})

    def test_prompt_constraints_for_unspecified_length_only_adds_safe_output_rule(self) -> None:
        prompt = output_prompt_constraints("polish", output_contract("polish", "保持原意。"))
        self.assertNotIn("目标约", prompt)
        self.assertNotIn("至少", prompt)
        self.assertIn("只返回正文", prompt)

    def test_prompt_constraints_are_noop_for_structured_tasks(self) -> None:
        self.assertEqual(output_prompt_constraints("scene", {}), "")

    def test_alias_and_shape_are_product_usable(self) -> None:
        result = validate_output(
            "continue",
            "甲，乙。",
            output_contract("continue", "续写4至5字。"),
            protocol_complete=True,
        )
        self.assertTrue(result["eligible"])
        self.assertIn("contract", result)
        self.assertIn("issues", result)
        self.assertEqual(result["output_contract"], result["contract"])


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(OutputContractTests)
    )
    print(json.dumps({"passed": outcome.wasSuccessful()}, ensure_ascii=False))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
