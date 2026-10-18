"""Project-scoped LangGraph persistence in the same transaction as queued jobs."""
from __future__ import annotations

import json
import threading
import uuid

from langgraph.checkpoint.base import (
    BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP, get_checkpoint_metadata,
)


def install_schema(conn):
    statements = (
        """CREATE TABLE IF NOT EXISTS app_agent_session(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id), chapter_id TEXT NOT NULL REFERENCES document_node(id),
          request_id TEXT NOT NULL, query TEXT NOT NULL, plan_json TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          restored_read_only INTEGER NOT NULL DEFAULT 0 CHECK(restored_read_only IN (0,1)),
          UNIQUE(project_id,branch_id,request_id))""",
        """CREATE TABLE IF NOT EXISTS app_agent_checkpoint(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id), session_id TEXT NOT NULL REFERENCES app_agent_session(id),
          checkpoint_key TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, parent_key TEXT,
          type TEXT NOT NULL, checkpoint BLOB NOT NULL, metadata TEXT NOT NULL,
          UNIQUE(session_id,checkpoint_ns,checkpoint_key))""",
        """CREATE TABLE IF NOT EXISTS app_agent_write(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id), session_id TEXT NOT NULL REFERENCES app_agent_session(id),
          checkpoint_key TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, task_id TEXT NOT NULL,
          task_path TEXT NOT NULL DEFAULT '', idx INTEGER NOT NULL, channel TEXT NOT NULL,
          type TEXT NOT NULL, value BLOB NOT NULL,
          UNIQUE(session_id,checkpoint_ns,checkpoint_key,task_id,idx))""",
    )
    for statement in statements:
        conn.execute(statement)


class ScopedCheckpointSaver(BaseCheckpointSaver):
    """The server binds scope; graph configuration can never select another session.

    LangGraph writes checkpoints on its executor threads. A private connection
    with check_same_thread=False is required. The HTTP owner commits once after
    invoke finishes, so graph progress and the queue outbox commit together.
    No remote request may start until that commit succeeds.
    """

    def __init__(self, conn, scope, session_id):
        super().__init__()
        self.conn = conn
        self.project = scope["project_id"]
        self.branch = scope["branch_id"]
        self.session_id = session_id
        self.thread_id = f"{self.project}:{self.branch}:{session_id}"
        self.lock = threading.RLock()

    def _scope(self, config):
        if config["configurable"].get("thread_id") != self.thread_id:
            raise ValueError("写作任务不属于当前作品。")
        row = self.conn.execute(
            "SELECT restored_read_only FROM app_agent_session WHERE id=? AND project_id=? AND branch_id=?",
            (self.session_id, self.project, self.branch),
        ).fetchone()
        if row is None or row[0]:
            raise ValueError("恢复的写作任务仅供回看，不能继续执行。")
        return config["configurable"].get("checkpoint_ns", "")

    def _config(self, namespace, key):
        return {"configurable": {"thread_id": self.thread_id,
                                 "checkpoint_ns": namespace, "checkpoint_id": key}}

    def get_tuple(self, config):
        with self.lock:
            namespace = self._scope(config)
            key = config["configurable"].get("checkpoint_id")
            query = "SELECT checkpoint_key,parent_key,type,checkpoint,metadata FROM app_agent_checkpoint WHERE session_id=? AND project_id=? AND branch_id=? AND checkpoint_ns=?"
            args = [self.session_id, self.project, self.branch, namespace]
            if key:
                query += " AND checkpoint_key=?"
                args.append(key)
            query += " ORDER BY checkpoint_key DESC LIMIT 1"
            row = self.conn.execute(query, args).fetchone()
            if row is None:
                return None
            key, parent, kind, value, metadata = tuple(row)
            writes = self.conn.execute(
                "SELECT task_id,channel,type,value FROM app_agent_write WHERE session_id=? AND project_id=? AND branch_id=? AND checkpoint_ns=? AND checkpoint_key=? ORDER BY task_path,task_id,idx",
                (self.session_id, self.project, self.branch, namespace, key),
            ).fetchall()
            return CheckpointTuple(
                self._config(namespace, key), self.serde.loads_typed((kind, value)),
                json.loads(metadata), self._config(namespace, parent) if parent else None,
                [(w[0], w[1], self.serde.loads_typed((w[2], w[3]))) for w in writes],
            )

    def put(self, config, checkpoint, metadata, new_versions):
        with self.lock:
            namespace = self._scope(config)
            kind, value = self.serde.dumps_typed(checkpoint)
            self.conn.execute(
                "INSERT INTO app_agent_checkpoint VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,checkpoint_ns,checkpoint_key) DO UPDATE SET type=excluded.type,checkpoint=excluded.checkpoint,metadata=excluded.metadata",
                ("agcp_" + uuid.uuid4().hex, self.project, self.branch, self.session_id,
                 checkpoint["id"], namespace, config["configurable"].get("checkpoint_id"),
                 kind, value, json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False)),
            )
            return self._config(namespace, checkpoint["id"])

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.lock:
            namespace = self._scope(config)
            replace = all(channel in WRITES_IDX_MAP for channel, _ in writes)
            conflict = " DO UPDATE SET channel=excluded.channel,type=excluded.type,value=excluded.value" if replace else " DO NOTHING"
            for idx, (channel, item) in enumerate(writes):
                kind, value = self.serde.dumps_typed(item)
                self.conn.execute(
                    "INSERT INTO app_agent_write VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,checkpoint_ns,checkpoint_key,task_id,idx)" + conflict,
                    ("agwr_" + uuid.uuid4().hex, self.project, self.branch, self.session_id,
                     config["configurable"]["checkpoint_id"], namespace, task_id, task_path,
                     WRITES_IDX_MAP.get(channel, idx), channel, kind, value),
                )

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.lock:
            namespace = self._scope(config)
            rows = self.conn.execute(
                "SELECT checkpoint_key FROM app_agent_checkpoint WHERE session_id=? AND project_id=? AND branch_id=? AND checkpoint_ns=? ORDER BY checkpoint_key DESC",
                (self.session_id, self.project, self.branch, namespace),
            ).fetchall()
            count = 0
            for row in rows:
                if before and row[0] >= before["configurable"]["checkpoint_id"]:
                    continue
                item = self.get_tuple(self._config(namespace, row[0]))
                if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                    continue
                if limit is not None and count >= limit:
                    break
                count += 1
                yield item
