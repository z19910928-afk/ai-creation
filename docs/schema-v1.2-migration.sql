-- Migration v1.2: 提示词工坊（author-editable prompt templates）.
--
-- 权威记录：`app_*` 应用表统一由 backend/server.py 的 init_db() 以
-- CREATE TABLE IF NOT EXISTS 创建，本文件只记录 v1.2 引入的 DDL，供审计与
-- 手工排查使用，不参与运行时启动。
--
-- 注意：不要在这里修改 `PRAGMA user_version` 或 migration_ledger。服务启动时
-- init_db() 仍按 v1.1 的 user_version=11 做完整性校验；提示词表属于应用层
-- 增量，缺失时由 init_db() 与 seed_builtin_templates() 自动补齐，无需提升
-- 文档库迁移级别。
BEGIN IMMEDIATE;

-- 提示词模板：一个「动作」对应一条可编辑正文。
--   action     稳定动作标识（例如 ai.task.continue、chat.system）
--   is_builtin 1 = 出厂内置模板；不可删除、不可直接改 body，只能重置或克隆
--   version    每次正文变更递增，便于前端提示「已修改」
CREATE TABLE IF NOT EXISTS app_prompt_template(
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  name TEXT NOT NULL,
  body TEXT NOT NULL,
  is_builtin INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 同一动作允许多条自定义模板（作者克隆多个版本），内置模板固定一条，
-- 渲染时按 is_builtin 降序、updated_at 升序取最后一条生效正文。
CREATE INDEX IF NOT EXISTS idx_app_prompt_template_action
  ON app_prompt_template(action, is_builtin);

COMMIT;
