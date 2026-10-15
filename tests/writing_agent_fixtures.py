#!/usr/bin/env python3
"""Offline contract tests for the LangGraph writing-agent coordinator.

These tests never configure a remote provider.  The optional graph tests run
when the pinned LangGraph runtime is installed; validation tests always run.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import moyu_writing_agent as agent

try:
    from langgraph.checkpoint.memory import InMemorySaver
    _LANGGRAPH = True
except ImportError:  # pragma: no cover - exercised in the dependency-free checkout.
    InMemorySaver = None
    _LANGGRAPH = False


class WritingAgentContractTests(unittest.TestCase):
    def test_initial_state_is_small_and_scoped(self):
        state = agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "continue", max_calls=3)
        self.assertEqual(state["status"], "planned")
        self.assertEqual(state["stage"], "preflight")
        self.assertEqual(state["calls_used"], 0)
        self.assertNotIn("body", state)
        self.assertNotIn("prompt", state)

    def test_inspect_requires_two_call_budget(self):
        with self.assertRaises(agent.WritingAgentContractError):
            agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "inspect", max_calls=1)
        state = agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "inspect", max_calls=2)
        self.assertEqual(state["mode"], "inspect")
        with self.assertRaises(agent.WritingAgentContractError):
            agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "inspect", max_calls=3)

    def test_polish_requires_three_call_budget(self):
        state = agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "polish", max_calls=3)
        self.assertEqual(state["mode"], "polish")
        with self.assertRaises(agent.WritingAgentContractError):
            agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "polish", max_calls=2)

    def test_invalid_scope_and_mode_are_rejected(self):
        with self.assertRaises(agent.WritingAgentContractError):
            agent.initial_state("", "book_1", "branch_1", "chapter_1", "continue", max_calls=3)
        with self.assertRaises(agent.WritingAgentContractError):
            agent.initial_state("wf_1", "book_1", "branch_1", "chapter_1", "chat", max_calls=3)

    def test_missing_langgraph_fails_explicitly(self):
        if _LANGGRAPH:
            self.skipTest("LangGraph is installed; dependency error is covered by the runtime")
        callbacks = agent.WritingAgentCallbacks(
            prepare=lambda _state: {},
            enqueue=lambda _state, _task, _step: {},
            get_job=lambda _state, _job: {},
            verify_adoption=lambda _state, _answer: {},
        )
        with self.assertRaises(agent.WritingAgentDependencyError):
            agent.build_graph(callbacks, None)


@unittest.skipUnless(_LANGGRAPH, "LangGraph optional runtime is not installed")
class WritingAgentGraphTests(unittest.TestCase):
    def setUp(self):
        self.jobs = {}
        self.enqueue_calls = []

        def prepare(_state):
            return {"status": "ready", "source_revision_id": "rev_1", "source_hash": "hash_1"}

        def enqueue(state, task, step_id):
            job_id = f"job_{len(self.enqueue_calls) + 1}"
            run_id = f"run_{len(self.enqueue_calls) + 1}"
            self.enqueue_calls.append((task, step_id))
            self.jobs[job_id] = {
                "id": job_id,
                "ai_run_id": run_id,
                "status": "queued",
                "result": {},
            }
            return {"id": job_id, "ai_run_id": run_id, "status": "queued"}

        def get_job(_state, job_id):
            return dict(self.jobs[job_id])

        def verify_adoption(_state, _answer):
            return {"applied": True, "ai_run_id": "run_1", "result_ref": "application_1",
                    "source_revision_id": "rev_2", "source_hash": "hash_2"}

        self.callbacks = agent.WritingAgentCallbacks(
            prepare=prepare,
            enqueue=enqueue,
            get_job=get_job,
            verify_adoption=verify_adoption,
        )
        self.checkpointer = InMemorySaver()
        self.graph = agent.build_graph(self.callbacks, self.checkpointer)
        self.state = agent.initial_state(
            "wf_1", "book_1", "branch_1", "chapter_1", "continue", max_calls=3
        )
        self.config = agent.graph_config("wf_1")

    def resume(self, value):
        return agent.invoke_resume(self.graph, "wf_1", value)

    def current(self):
        return dict(self.graph.get_state(self.config).values)

    def test_continue_pauses_at_authorization_then_candidate_adoption_then_review_ready(self):
        started = agent.invoke_start(self.graph, self.state)
        self.assertIn("__interrupt__", started)
        self.assertEqual(self.current()["status"], "awaiting_authorization")

        waiting = self.resume({"action": "authorize"})
        self.assertIn("__interrupt__", waiting)
        self.assertEqual(self.current()["stage"], "continue_generation")
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported",
                       "candidate_hash": "candidate_1"},
        })
        adoption = self.resume({"action": "advance"})
        self.assertIn("__interrupt__", adoption)
        self.assertEqual(self.current()["status"], "awaiting_adoption")
        self.assertEqual(self.current()["stage"], "candidate_adoption")

        analysis_wait = self.resume({"action": "adopt"})
        self.assertIn("__interrupt__", analysis_wait)
        self.assertEqual(self.current()["current_task"], "chapter_analysis")
        self.assertEqual(self.current()["source_revision_id"], "rev_2")
        self.assertEqual(self.current()["source_hash"], "hash_2")
        analysis_job = self.current()["current_job_id"]
        self.jobs[analysis_job].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported",
                       "candidate_hash": "analysis_1", "analysis": {"id": "analysis_1"}},
        })
        review_wait = self.resume({"action": "advance"})
        self.assertIn("__interrupt__", review_wait)
        self.assertEqual(self.current()["current_task"], "semantic_review")
        review_job = self.current()["current_job_id"]
        self.jobs[review_job].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported",
                       "candidate_hash": "review_1", "story_review": {"id": "review_1"}},
        })
        finished = self.resume({"action": "advance"})
        self.assertNotIn("__interrupt__", finished)
        self.assertEqual(self.current()["status"], "review_ready")
        self.assertEqual([task for task, _step in self.enqueue_calls],
                         ["continue", "chapter_analysis", "semantic_review"])

    def test_unknown_job_stops_without_enqueueing_another_task(self):
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "status": "unknown",
            "error": "provider result unknown",
            "result": {"safe_to_apply": False},
        })
        result = self.resume({"action": "advance"})
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(self.current()["status"], "requires_action")
        self.assertEqual(len(self.enqueue_calls), 1)
        self.assertIn("unknown", self.current()["error"])

    def test_polish_waits_for_author_before_analyzing_revised_manuscript(self):
        self.state = agent.initial_state(
            "wf_1", "book_1", "branch_1", "chapter_1", "polish", max_calls=3
        )
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        self.assertEqual(self.current()["stage"], "polish_generation")
        self.assertEqual(self.current()["current_task"], "polish")
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete",
                       "billing_state": "usage_reported", "candidate_hash": "revised_1"},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["status"], "awaiting_adoption")
        self.assertEqual([task for task, _step in self.enqueue_calls], ["polish"])
        self.resume({"action": "applied"})
        self.assertEqual(self.current()["current_task"], "chapter_analysis")
        self.assertEqual(self.current()["source_revision_id"], "rev_2")
        self.assertEqual(self.current()["calls_used"], 2)

    def test_inspect_uses_only_two_analysis_tasks(self):
        self.state = agent.initial_state(
            "wf_1", "book_1", "branch_1", "chapter_1", "inspect", max_calls=2
        )
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        self.assertEqual(self.current()["current_task"], "chapter_analysis")
        first_job = self.current()["current_job_id"]
        self.jobs[first_job].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported",
                       "analysis": {"id": "analysis_1"}},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["current_task"], "semantic_review")
        second_job = self.current()["current_job_id"]
        self.jobs[second_job].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported",
                       "story_review": {"id": "review_1"}},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["status"], "review_ready")
        self.assertEqual(self.current()["calls_used"], 2)
        self.assertEqual([task for task, _step in self.enqueue_calls],
                         ["chapter_analysis", "semantic_review"])

    def test_pending_job_advance_reads_same_job_without_another_enqueue(self):
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        original_job = self.current()["current_job_id"]
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["current_job_id"], original_job)
        self.assertEqual(self.current()["status"], "awaiting_job")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_wrong_scope_job_is_blocked(self):
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "project_id": "another_book",
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported"},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["status"], "requires_action")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_incomplete_success_and_unknown_billing_do_not_reach_adoption(self):
        for outcome in (
            {"safe_to_apply": True, "completion_state": "partial", "billing_state": "usage_reported"},
            {"safe_to_apply": True, "completion_state": "complete", "billing_state": "unknown"},
            {"safe_to_apply": True, "completion_state": "complete"},
            {"safe_to_apply": True, "completion_state": "complete", "billing_state": None},
            {"safe_to_apply": True, "completion_state": "complete", "billing_state": "unrecognized"},
            {"safe_to_apply": True, "completion_state": "complete", "billing_state": ["unknown"]},
            {"safe_to_apply": True, "completion_state": "complete",
             "billing_state": "usage_reported", "output_validation": {"eligible": False}},
        ):
            with self.subTest(outcome=outcome):
                graph = agent.build_graph(self.callbacks, InMemorySaver())
                agent.invoke_start(graph, self.state)
                agent.invoke_resume(graph, "wf_1", {"action": "authorize"})
                state = dict(graph.get_state(self.config).values)
                before = len(self.enqueue_calls)
                self.jobs[state["current_job_id"]].update({"status": "success", "result": outcome})
                agent.invoke_resume(graph, "wf_1", {"action": "advance"})
                state = dict(graph.get_state(self.config).values)
                self.assertEqual(state["status"], "requires_action")
                self.assertEqual(len(self.enqueue_calls), before)

    def test_verify_adoption_requires_original_run_and_no_analysis_enqueue_on_failure(self):
        self.callbacks = agent.WritingAgentCallbacks(
            prepare=self.callbacks.prepare,
            enqueue=self.callbacks.enqueue,
            get_job=self.callbacks.get_job,
            verify_adoption=lambda _state, _answer: {
                "applied": True, "ai_run_id": "another_run", "result_ref": "application_x",
            },
        )
        self.graph = agent.build_graph(self.callbacks, self.checkpointer)
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported"},
        })
        self.resume({"action": "advance"})
        self.resume({"action": "adopt"})
        self.assertEqual(self.current()["status"], "requires_action")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_analysis_without_persisted_record_stops(self):
        self.state = agent.initial_state(
            "wf_1", "book_1", "branch_1", "chapter_1", "inspect", max_calls=2
        )
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        job_id = self.current()["current_job_id"]
        self.jobs[job_id].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported"},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["status"], "requires_action")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_recompiled_graph_recovers_same_checkpoint_without_reenqueue(self):
        agent.invoke_start(self.graph, self.state)
        self.resume({"action": "authorize"})
        original_job = self.current()["current_job_id"]
        self.graph = agent.build_graph(self.callbacks, self.checkpointer)
        self.assertEqual(self.current()["current_job_id"], original_job)
        self.jobs[original_job].update({
            "status": "success",
            "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported"},
        })
        self.resume({"action": "advance"})
        self.assertEqual(self.current()["stage"], "candidate_adoption")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_preflight_block_does_not_authorize_or_enqueue(self):
        callbacks = agent.WritingAgentCallbacks(
            prepare=lambda _state: {"status": "blocked", "error": "材料已经变化"},
            enqueue=self.callbacks.enqueue,
            get_job=self.callbacks.get_job,
            verify_adoption=self.callbacks.verify_adoption,
        )
        graph = agent.build_graph(callbacks, InMemorySaver())
        result = agent.invoke_start(graph, self.state)
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["status"], "requires_action")
        self.assertEqual(self.enqueue_calls, [])

    def test_enqueue_source_failure_is_terminal_without_another_enqueue(self):
        calls = []

        def refused_enqueue(_state, _task, _step_id):
            calls.append(True)
            raise ValueError("当前稿件已经变化")

        callbacks = agent.WritingAgentCallbacks(
            prepare=self.callbacks.prepare,
            enqueue=refused_enqueue,
            get_job=self.callbacks.get_job,
            verify_adoption=self.callbacks.verify_adoption,
        )
        graph = agent.build_graph(callbacks, InMemorySaver())
        agent.invoke_start(graph, self.state)
        result = agent.invoke_resume(graph, "wf_1", {"action": "authorize"})
        self.assertEqual(result["status"], "requires_action")
        self.assertEqual(result["calls_used"], 0)
        self.assertEqual(calls, [True])
        repeated = agent.invoke_resume(graph, "wf_1", {"action": "authorize"})
        self.assertEqual(repeated["status"], "requires_action")
        self.assertEqual(calls, [True])

    def test_adoption_without_new_revision_stops_before_analysis(self):
        callbacks = agent.WritingAgentCallbacks(
            prepare=self.callbacks.prepare,
            enqueue=self.callbacks.enqueue,
            get_job=self.callbacks.get_job,
            verify_adoption=lambda _state, _answer: {
                "applied": True, "ai_run_id": "run_1",
                "source_revision_id": "rev_1", "source_hash": "hash_1",
            },
        )
        graph = agent.build_graph(callbacks, InMemorySaver())
        agent.invoke_start(graph, self.state)
        agent.invoke_resume(graph, "wf_1", {"action": "authorize"})
        state = dict(graph.get_state(self.config).values)
        self.jobs[state["current_job_id"]].update({
            "status": "success", "result": {"safe_to_apply": True, "completion_state": "complete", "billing_state": "usage_reported"},
        })
        agent.invoke_resume(graph, "wf_1", {"action": "advance"})
        result = agent.invoke_resume(graph, "wf_1", {"action": "applied"})
        self.assertEqual(result["status"], "requires_action")
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_cancel_at_authorization_is_terminal(self):
        agent.invoke_start(self.graph, self.state)
        result = self.resume({"action": "cancel"})
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(self.current()["status"], "canceled")
        self.assertEqual(self.enqueue_calls, [])


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=2))
