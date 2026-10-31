# 当前字节 verdict 台账（2026-10-31）

本文件把历史 content-bound verdict 与 `docs/current-bytes-manifest-2026-10-30.json` 的 22 个当前输入逐族对齐。
它只做**可复用性判定**，不重跑旧验收、不修改旧任务状态、不构成发布放行。

## 1. 判定规则

- 只有旧 verdict 绑定的全部输入 `sha256` 与行数都与 manifest **完全一致**，才可按 content-bound 复用。
- 任一文件漂移，旧 verdict 就只能作为历史证据；必须针对当前字节重新验收，不能把旧 pass 平移到当前。
- 本文件按“至少有一个输入漂移”判定为 `stale`；没有把任何旧 verdict 改写成当前通过。

## 2. 当前 22 个输入

| 文件 | 行数 | sha256 |
| --- | ---: | --- |
| `backend/moyu_continuation.py` | 899 | `4486b78398b6c826946083df08c8b888575ae8c3bfb1ca3a1219b4aaca71a874` |
| `backend/server.py` | 8198 | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` |
| `benchmarks/budgeted/run.py` | 3782 | `549a744afbdf49811826af258f2873d694b2cc06a265562f8fa8e4bc64ffa2c6` |
| `benchmarks/budgeted/capable.py` | 284 | `7059326560ee77e20e38f4bae786755fff36c2e0aee6d8b787a2b1105ed949eb` |
| `benchmarks/budgeted/results/budgeted-capable.json` | 815 | `150467607934ad8175e0112371044cb802beaee545c59f6c972afc6cd673c868` |
| `benchmarks/budgeted/results/budgeted-capable.md` | 24 | `c04bb5a1df3716b86da328430a8180b491c78e37aac9415af66b66d068c4c7bf` |
| `tests/q1_capable_fixtures.py` | 67 | `b2557da7cb5a54e547998659e29f307ef9d3992d0892e76ae94716aeac6c49ac` |
| `tests/current_bytes_fixtures.py` | 66 | `1d4562e16ef9724333e8fc31c4f966eddb1d98b8d3405388f423a4fa3501f55d` |
| `scripts/verify_manifest.py` | 85 | `9e383cdf2a5c80e6c1f57d5760eaa803e3b8d2e81e990a45cbd7824be57ee132` |
| `frontend/app.js` | 1906 | `e22c79e857542936b9c272bf36f90b2c142805e61cf009a6297d838291c05b58` |
| `frontend/styles.css` | 550 | `fb2b661f48f80d93218ad29e1306d6ed5c070252c9357b7a816da0c50d4d59b2` |
| `docs/product-contract-soft-target-2026-10-30.md` | 38 | `32841501994469534932924e40a5650faac6102b31e14e14d6efc525810b1016` |
| `backend/moyu_consistency.py` | 1222 | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` |
| `backend/moyu_extract.py` | 1041 | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` |
| `benchmarks/real_corpus/extract.py` | 301 | `2e046994280e26beaa89c5b9f194c992c341ae057efeb64ed0674f5a5de8e201` |
| `tests/budgeted_continuation_fixtures.py` | 1284 | `a8cb9cf7070fa425f091c3ea956e4f40ff69ba093ef56e554abc3edc63d47165` |
| `tests/budgeted_continuation_http_fixtures.py` | 937 | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` |
| `tests/consistency_fixtures.py` | 640 | `043b6ac5da07f69e61cd5d8c09f735b5990c7fab7e1af98b4c3b8075549b8f7a` |
| `tests/memory_assertion_predicate_fixtures.py` | 355 | `f6eeff0dede105e6e76ea4bef14538eb6534ab65e1e382fed7727723a9c4b3c8` |
| `tests/extract_fixtures.py` | 1372 | `f48b4f191d7fa390962539dd69c1a49453354351c3615350bf2246db1f2f71b5` |
| `docs/consistency-budget-v1.2-2026-10-23.md` | 187 | `ee5b8bdbcae258b3297b9c7f0cfe0db0a6999b72cac1e2a9bde4aa3decd07280` |
| `docs/real-corpus-extract-2026-10-24.md` | 317 | `141a8dce34832587089574d89e366632d010f64c806f84e45d363eb3b6a08710` |

## 3. 历史 verdict 家族与当前判定

| 旧 verdict 家族 | 旧绑定输入（示例） | 当前输入 | 判定 |
| --- | --- | --- | --- |
| t18 / t22 / t34 / t35 / t37 / t39 / t43 / t44 / t48 / t49 / t51 / t66 | `backend/server.py` 的 `5024a6a5`、`2bd6e253`、`a70a25ce`、`c696fa8d`、`d16a964c`、`319b4346` 等 | `f74c885f…` | **stale**：旧 hash 与当前不同，不能复用 |
| t22 / t34 / t36 / t39 / t48 | `backend/moyu_continuation.py` 的 `583059fe` | `4486b783…` | **stale** |
| t27 / t34 / t38 / t43 / t48 | `backend/moyu_consistency.py` 的 `39dee239`、`a5a88b06`；`tests/consistency_fixtures.py` 的 `040303f5`、`05ef1cf6` | `b855e910…` / `043b6ac5…` | **stale** |
| t54 / t60 / t63 / t65 / t66 / t68 / t70 / t72 / t74 / t76 | `backend/moyu_extract.py` 的 `b26c4ea9`、`4c047cb1`、`d4a06035`、`4457a0e7`、`42051256`、`da5a5d19`、`46ee2337`、`5e686029`；`benchmarks/real_corpus/extract.py` 的 `a9e23053`；`tests/extract_fixtures.py` 的 `f0c156ad`…`9361f66e` | `6b5d4313…` / `2e046994…` / `f48b4f19…` | **stale**：全部旧 hash 均不等于当前 |
| t22 / t34 / t36 / t39 / t48 | `tests/budgeted_continuation_fixtures.py` 的 `fda39caa`；`tests/budgeted_continuation_http_fixtures.py` 的 `e6e6a96f` | `a8cb9cf7…` / `67e0aa21…` | **stale** |
| t34 / t37 | `tests/memory_assertion_predicate_fixtures.py` 的 `10719789` | `f6eeff0d…` | **stale** |
| t54 / t60 / t63 / t65 / t66 / t68 / t70 / t72 / t74 / t76 / t87 | `docs/real-corpus-extract-2026-10-24.md` 的 `409bba30`、`fda516f4`、`9a4ff063`、`1f59286d`、`cbdf44c6`、`5c532092`、`7b766bcf` 等；`docs/consistency-budget-v1.2-2026-10-23.md` 的 `e711a113` | `141a8dce…` / `ee5b8bdb…` | **stale**；文档不在“可复用”例外内 |
| t79 | `budgeted-v1.2-baseline.json` 的对抗式 7/9 口径 | 当前 capable arm 与旧 baseline | **不重开、不改写**；旧 7/9 保留，capable 臂另证 |
| t88 | `tests/desktop_smoke.cjs` 旧焦点行为 | 当前同机报告与 CI | **failed 保留**；没有 26/26 全量声明 |
| t91 / t96 | 旧发布说明/报告字节 | 当前文档链 | **未完成/失败保留**；不得补写 pass |
| t9 / t29 / t31 / t32 / t33 / t93 / t94 / t97 / t100 / t102 / t104 | 未记录 artifact identity 的旧验收 | 本 manifest 22 文件 | **不可复用**；只能作为历史证据，若要当前结论须重新验收 |

## 4. 结论

- 本次检查的旧 verdict **没有任何一个**可以仅凭旧 pass 复用；它们至少有一个绑定文件已漂移。
- 旧任务状态没有改变：`t79` 仍 failed，`t88` 仍 failed，`t91`/`t96` 仍未完成；本文件不把它们改成 pass。
- 当前 22 个输入由 `scripts/verify_manifest.py` 与 `tests/current_bytes_fixtures.py` 在执行时重算；漂移会硬失败。
- 该台账是“历史 verdict 不与当前字节脱节”的治理记录，不是 Q1 达标、B4 关闭或发布放行。

## 5. 复算

```text
.venv/bin/python scripts/verify_manifest.py
.venv/bin/python tests/current_bytes_fixtures.py
```

相关证据：`docs/verification-q1-capable-2026-10-30.md`、`docs/current-bytes-manifest-2026-10-30.json`。
