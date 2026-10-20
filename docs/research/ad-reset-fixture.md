# AD_RESET 合成 fixture 証拠台帳（Issue #293）

スロット `$10`、候補エントリ `$E030`、親 #25。**状態: ABI は未確定（BLOCKED_ABI）。`release_eligible=false`。callee（System Card の AD_RESET）は未実行。**

`tools/adreset_fixture.py` は、ADPCM コントローラのリセットを**自作の合成モデル**で表し、判定器が範囲外・未許可の操作を PASS にしないことを確かめる。モデルの挙動は本リポジトリの仮定であり、BIOS や実機の測定ではない。ABI の根拠は [ad-reset-contract.md](ad-reset-contract.md) に従う。

## 1. 判定結果の意味

| 判定 | 意味 |
| --- | --- |
| `MODEL_OK` | 合成モデルと観測記録が整合する。callee の PASS ではない。 |
| `FAIL` | 合成モデルの期待（IDLE、busy=0、予算内、中断の有無）と観測が食い違う。 |
| `REJECTED` | 範囲外のスロット・操作、実行・副作用・純正 BIOS JSR の要求。 |
| `BLOCKED` | 入力の形式不正、または観測記録がない。 |

CLI は判定を出した後も常に終了コード 3 を返す。ABI が未確定のため、いかなる実行も 0 で終えない。

## 2. 検査内容と限界

| 区分 | 内容 | 検証した範囲 | 限界 |
| --- | --- | --- | --- |
| 正例 | 通常リセット（IDLE）、転送中・再生中のリセット境界 | 合成モデルが IDLE・busy=0・予算内に戻ること | モデルの仮定であり、実機の動作ではない |
| 負例 | busy 残存、再生の継続、予算超過、ポール数 0 や bool、中断フラグの誤り | 判定器が FAIL にすること | 合成の改ざんのみ。実記録の誤りは未検証 |
| 拒否 | 他スロットのエントリ、他操作、実行許可、副作用、I/O、純正 BIOS JSR | REJECTED にすること | 入力フラグの検査のみ |
| 不正入力 | 未知の状態、観測なし | BLOCKED にすること | — |

## 3. 証拠の区分

| 区分 | 状態 | 内容 |
| --- | --- | --- |
| 合成ホストテスト | 実施 | `python -I -m unittest discover -s tests/host -p test_adreset_fixture.py -v`（6 件） |
| 独立レビュー | NOT_RUN | 別エージェントによる台帳レビューは行っていない。 |
| provenance 審査 | PENDING | §2 の出典は本 PR で追加していない。 |
| callee 実行 | NOT_RUN | System Card の AD_RESET を実行していない。 |
| 実機・第 2 エミュレータ | NOT_TESTED | — |

## 4. 残作業

- callee を実行できる実コア harness の整備後、[ad-reset-contract.md](ad-reset-contract.md) の行 3b〜19b を同じ条件で測定する。
- 合成モデルの仮定（busy 解除の 1 poll、予算 4）は、実測で置き換える前提の仮置きであり、確定値として扱わない。
