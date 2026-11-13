# Pre-seal repair log

## Authoring agent runs

### Run 1
- mode author
- run_id author-1-8dd6e5be
- started_utc 2026-11-12T02:58:18Z
- claude_code_version 2.1.302
- model claude-sonnet-5-5
- guide_sha256 7a8081614f7d18d0827a3861a88ec8129b09cea707c0d770909ccd23c1d4dcba
- fixtures_py_sha256 6c350647249e10e9fcddd470d734339969bde8fd533dfa3080f16b894763c643
- fixtures_py_commit 6c61b304a4e3ace25eac553fe1965f5adccb8514
- prompt_sha256 e31490aac8190f35972fcb949ddbdcbf944472dcb7a4da6698fba26f937cdd5c
- cost_usd 0.7698
- stop none
- sync new 20, fixed 0, ignored 0, changed_unasked 0

### Run 2
- mode author
- run_id author-2-415e7ed9
- started_utc 2026-11-12T03:12:01Z
- claude_code_version 2.1.302
- model claude-sonnet-5-5
- guide_sha256 7a8081614f7d18d0827a3861a88ec8129b09cea707c0d770909ccd23c1d4dcba
- fixtures_py_sha256 6c350647249e10e9fcddd470d734339969bde8fd533dfa3080f16b894763c643
- fixtures_py_commit 6c61b304a4e3ace25eac553fe1965f5adccb8514
- prompt_sha256 79e0a6e3fa16eff45abc867053a61769ff178fd454287ca83c4cdd189217fbab
- cost_usd 0.3828
- stop breach
- sync none recorded

### Run 3
- mode author
- run_id author-3-4ad37de5
- started_utc 2026-11-12T03:17:31Z
- claude_code_version 2.1.302
- model claude-sonnet-5-5
- guide_sha256 7a8081614f7d18d0827a3861a88ec8129b09cea707c0d770909ccd23c1d4dcba
- fixtures_py_sha256 6c350647249e10e9fcddd470d734339969bde8fd533dfa3080f16b894763c643
- fixtures_py_commit 6c61b304a4e3ace25eac553fe1965f5adccb8514
- prompt_sha256 79e0a6e3fa16eff45abc867053a61769ff178fd454287ca83c4cdd189217fbab
- cost_usd 0.3882
- stop breach
- sync none recorded

### Run 4
- mode repair
- run_id author-4-c3226ff9
- started_utc 2026-11-12T21:59:27Z
- claude_code_version 2.1.302
- model claude-sonnet-5-5
- guide_sha256 7a8081614f7d18d0827a3861a88ec8129b09cea707c0d770909ccd23c1d4dcba
- fixtures_py_sha256 6c350647249e10e9fcddd470d734339969bde8fd533dfa3080f16b894763c643
- fixtures_py_commit 6c61b304a4e3ace25eac553fe1965f5adccb8514
- prompt_sha256 c01b274e514479be5587ba5de1614af0275a2f61d2ce74d2d0af3d89eb2b826e
- cost_usd 0.1334
- stop breach
- sync none recorded

- manual_sync_A3_record_sha256 86e1154bf01226ba6e5175594c6afdf370eda406a22ba6ead111694c1f88dc47

## Repair round

- fixture heldout-024-parcelwise probes 1

## Repairs

- repair fixture heldout-024-parcelwise probe p4 fields question, answer, item.oracle_line; floor_correct 0/3; oracle_correct 0/3; reasons oracle_minority
  - question old_sha256 497fc547398cc092a89f1c2cf986bc52880ee3458ce47c909306ad3ae0411d97 new_sha256 814fffd8ed64c22877da87e268507f3017cbfc50dcabdc788997990da9c5e689
  - answer old_sha256 a2fb45bd2be25144096c697556dd8798796d7549db46294942c9bf3f62f0104b new_sha256 259d70b80e4a5021763e828d0d7e8e2733513436787b91e386ddfc6337dd56ea
  - item.oracle_line old_sha256 bc77694607ea4e3770a8a7eda1c3646173b0d09f397065aa081bbe77867881a6 new_sha256 050dff2eb2202d4a52eb23ebe1114cc481b04a9edb2c2a26ef9de0e1ac9630d1

## Superseded sessions

- superseded heldout-024-parcelwise arms/floor/s1.md sha256 095b274cb2018cd427ef0b0656aea47f724826ef1ff5100887f9440ca7db512d
- superseded heldout-024-parcelwise arms/floor/s2.md sha256 095b274cb2018cd427ef0b0656aea47f724826ef1ff5100887f9440ca7db512d
- superseded heldout-024-parcelwise arms/floor/s3.md sha256 095b274cb2018cd427ef0b0656aea47f724826ef1ff5100887f9440ca7db512d
- superseded heldout-024-parcelwise arms/oracle/s1.md sha256 1ccdba255bd848d381885869bdfd23df9cc4cc4c2f5c94d5705f6eac1f0ff3f0
- superseded heldout-024-parcelwise arms/oracle/s2.md sha256 1ccdba255bd848d381885869bdfd23df9cc4cc4c2f5c94d5705f6eac1f0ff3f0
- superseded heldout-024-parcelwise arms/oracle/s3.md sha256 1ccdba255bd848d381885869bdfd23df9cc4cc4c2f5c94d5705f6eac1f0ff3f0
- superseded heldout-024-parcelwise probes/floor/s1.json sha256 574dc49e63af53171565da4e7a5bd157800973d24b74f542a81d974db769a8d7
- superseded heldout-024-parcelwise probes/floor/s2.json sha256 91f4a24bf172699ab21ad604af3e865ba8f46fe732df4cf795cbabd4d8a46639
- superseded heldout-024-parcelwise probes/floor/s3.json sha256 f6b901e5d5796634eead91ea4632fe134817f9dffc31ec88a64486758fec0224
- superseded heldout-024-parcelwise probes/oracle/s1.json sha256 c1a20ddcbbd52579161f1ea1ab5363e31b0fb5811482f87b9264c93d9307eb27
- superseded heldout-024-parcelwise probes/oracle/s2.json sha256 d1d354b4a675e4cad8788e95d1ddd9e33efc90e47ff95c880e021c4749500498
- superseded heldout-024-parcelwise probes/oracle/s3.json sha256 e4ef9759d104bb1244d84e1410ba602d520b3c1f77b653fa379ab6d1b0364635
- recheck raw_run_id 20261112T210153Z-ede5e0cd

## Flags

- no probe flagged

## Fixtures dropped or replaced

No fixture was dropped or replaced.
