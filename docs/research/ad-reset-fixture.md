# AD_RESET 合成 fixture 証拠台帳（Issue #293）

スロット `$10`、候補エントリ `$E030`、親 #25。**状態: ABI は未確定（BLOCKED_ABI）。`release_eligible=false`。callee（System Card の AD_RESET）は未実行。**

`tools/adreset_fixture.py` は、ADPCM コントローラのリセットを**自作の合成モデル**で表し、判定器が範囲外・未許可の操作を PASS にしないことを確かめる。モデルの挙動は本リポジトリの仮定であり、BIOS や実機の測定ではない。ABI の根拠は [ad-reset-contract.md](ad-reset-contract.md) に従う。

## 1. 判定結果の意味

| 判定 | 意味 |
| --- | --- |
| `MODEL_OK` | 合成モデルと観測記録が整合する。callee の PASS ではない。 |
| `FAIL` | 合成モデルの期待（IDLE、busy=0、予算内、中断の有無、観測の entry）と観測の値が食い違う。型は正しく、値だけが範囲外の場合。 |
| `REJECTED` | 範囲外のスロット・操作、実行・副作用・純正 BIOS JSR の要求。 |
| `BLOCKED` | 入力の形式不正（型違い: bool・None・float・文字列。例: `polls=True`、`execution_allowed=0`、`entry=57392.0`）、または観測記録がない。型は先に検査するため、型違いが FAIL になることはない。 |

CLI は出力の先頭に `[synthetic model, not a callee result]` を出し、判定を出した後も常に終了コード 3 を返す。ABI が未確定のため、いかなる実行も 0 で終えない。

## 2. 検査内容と限界

| 区分 | 内容 | 検証した範囲 | 限界 |
| --- | --- | --- | --- |
| 正例 | 通常リセット（IDLE）、転送中・再生中のリセット境界 | 合成モデルが IDLE・busy=0・予算内に戻ること | モデルの仮定であり、実機の動作ではない |
| 負例 | busy 残存、再生の継続、予算超過、ポール数 0、中断フラグの誤り、観測の entry の不一致 | 判定器が FAIL にすること | 合成の改ざんのみ。実記録の誤りは未検証 |
| 拒否 | 他スロットのエントリ、他操作、実行許可、副作用、I/O、純正 BIOS JSR | REJECTED にすること | 入力フラグの検査のみ |
| 不正入力 | 未知の状態、観測なし、型違い（bool・None・float・文字列） | BLOCKED にすること | — |

## 3. 証拠の区分

| 区分 | 状態 | 内容 |
| --- | --- | --- |
| 合成ホストテスト | 実施 | `python -I -m unittest discover -s tests/host -p test_adreset_fixture.py -v`（13 件） |
| 独立レビュー | CONDITIONAL | PR #343 のレビューで指摘（型違いの扱い、非 bool フラグ、entry の型、観測 entry の不一致）。指摘は本修正で反映済み。反映後の再レビューは別途行う。 |
| provenance 審査 | PENDING | §2 の出典は本 PR で追加していない。`ENTRY_CANDIDATE = 0xE030` は [ad-reset-contract.md](ad-reset-contract.md) §2.1 H1（HuCC `include/hucc/pcengine.inc` 485 行の宣言）に由来する呼出側の SOURCE_FACT。出典の provenance は PENDING のままで、この事実は BIOS の振る舞いを示さない。 |
| callee 実行 | NOT_RUN | System Card の AD_RESET を実行していない。 |
| 実機・第 2 エミュレータ | NOT_TESTED | — |

## 4. 残作業

- callee を実行できる実コア harness の整備後、[ad-reset-contract.md](ad-reset-contract.md) の行 3b〜19b を同じ条件で測定する。
- 合成モデルの仮定（busy 解除の 1 poll、予算 4）は、実測で置き換える前提の仮置きであり、確定値として扱わない。
