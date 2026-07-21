# delegate-to-claude

[English](README.md)

[![CI](https://github.com/wakamiya0315/delegate-to-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/wakamiya0315/delegate-to-claude/actions/workflows/ci.yml)

> [!WARNING]
> **Experimental / macOS-first。** このプロジェクトは初期releaseであり、production
> のsecurity boundaryではありません。自動委譲は利用者の既存Claude Code account
> からrequestを送信し、Claudeのquotaとrate limitを消費します。契約planやAPI設定
> によっては料金が発生します。v0.2では定型的なcodingを以前より積極的に委譲
> します。v0.2.1では監督役がBashを明示的に要求できますが、worker自身のstrict
> Claude Code sandbox内でだけ許可します。すでにsandbox内にあるnested sessionは
> macOSでfail closedする場合があります。更新後は利用量とBash receiptを確認して
> ください。

`delegate-to-claude` は、Codex または Claude Code を監督者として維持した
まま、明確に限定されたリポジトリ作業を新しい Claude Code Sonnet worker
へ委譲する、両環境共通の Agent Skill です。

worker は小規模な実装、テスト作成・実行、失敗原因の調査、コードレビューを
担当できます。定型作業は短いQuick promptだけで委譲でき、安全条件と検証条件は
launcherが補います。タスクの切り分け、差分レビュー、独立した再検証、最終承認
は常に監督者が担当します。

## 目的

上位モデルはアーキテクチャ、曖昧な判断、最終レビューでは有用ですが、機械的な
工程まで全て担当させるとトークンやレートリミットを多く消費します。このSkill
は、テスト追加、複数ファイル変更、非自明なmodule作成、失敗原因調査、focused
refactorやreview、3回を超えるrepository操作が見込まれる場合、編集前の委譲を
監督者へ求めます。

監督者が直接編集するのは、1ファイル約10行以下、テスト変更と調査が不要、仕様が
明確で低リスクという条件を全て満たす場合だけです。

アーキテクチャ、プロダクト判断、セキュリティ上重要な変更、大規模移行、秘密
情報、外部副作用、最終承認は監督者に残します。

## 必要環境

- macOS、または Claude Code sandbox の依存関係を導入した Linux
- 認証済みの Claude Code 2.1.205 以降
- Python 3.9 以降
- Git
- Agent Skills に対応する Codex または Claude Code
- Claude CodeからAnthropicへのcontrol-plane通信を許可した監督者sandbox

launcher は既存の Claude Code ログインを使用します。APIキーの取得、表示、
保存、変更は行いません。network許可は `claude` processがAnthropicへ接続する
ためだけに使われ、worker toolからのnetwork accessは引き続き拒否します。

## インストール

リポジトリを永続的に保持する場所へcloneし、個人Skill用のsymlinkを作成します。

```bash
git clone https://github.com/wakamiya0315/delegate-to-claude.git
cd delegate-to-claude
python3 scripts/install.py --target both
```

片方だけへ導入する場合は `--target codex` または `--target claude` を使います。
`--dry-run` を付けると、ファイルシステムを変更せず導入内容を確認できます。

installer は同じSkill正本を次の場所へリンクします。

- `~/.agents/skills/delegate-to-claude`（現在のCodex USER Skill配置先）
- `~/.codex/skills/delegate-to-claude`（従来Codexとの互換用）
- `~/.claude/skills/delegate-to-claude`

既存のファイル、ディレクトリ、別のsymlinkは上書きしません。初回導入後は
Codexを再起動してください。Claude Code起動時に個人skillsディレクトリが存在
しなかった場合は、Claude Codeも再起動します。以後のリポジトリ更新はsymlink
経由で反映されます。

## 更新

release notesと変更内容を確認してから、永続cloneをfast-forwardし、冪等なinstaller
を再実行します。

```bash
git pull --ff-only
python3 scripts/install.py --target both
```

導入先はsymlinkなので、checkoutした変更は新しいCodex／Claude Code sessionから
有効になります。再現性を優先する場合は、`main`を追従せずrelease tagへ固定して
ください。

## アンインストール

導入したsymlinkだけを削除します。次のcommandは、同じpathにある通常fileや
directoryを削除しません。

```bash
for link in \
  "$HOME/.agents/skills/delegate-to-claude" \
  "$HOME/.codex/skills/delegate-to-claude" \
  "$HOME/.claude/skills/delegate-to-claude"
do
  if [ -L "$link" ]; then
    unlink "$link"
  fi
done
```

symlinkがなくなったことを確認後、不要であれば永続cloneを別途削除してください。

## Codexから使う

短い指示で明示的にSkillを呼び出せます。

```text
$delegate-to-claude を使って、src/parser.pyへCSV検証を追加し関連テストも更新して。
```

CodexはSkillを自動選択することもあります。descriptionは複数ファイル、テスト
作成、診断、refactor、reviewでは編集前の委譲を優先するよう調整しています。
ただし暗黙選択は強制機構ではなく、最終的にはモデルの判断です。

`workspace-write` の非対話 `codex exec` では、Claude control-plane通信を許可
してください。receiptを警告なしで保存する場合はcache directoryも追加します。

```bash
codex exec --sandbox workspace-write \
  -c sandbox_workspace_write.network_access=true \
  --add-dir ~/Library/Caches/delegate-to-claude \
  '$delegate-to-claude を使って、この限定された変更をレビューして。'
```

## Claude Codeから使う

同じSkillを明示的に呼び出します。

```text
/delegate-to-claude src/parser.pyへCSV検証を追加し関連テストも更新して
```

Claude Codeも同じバランス型基準でSkillを自動的に読み込む場合があります。
Claude Code自身が監督者の場合でも、Skillは別の非対話 `claude -p` processを
起動し、新しいcontextと固定されたSonnetモデルをworkerへ与えます。

## launcherを直接使う

定型作業は短いQuick promptだけで実行できます。最小scope、受け入れ・検証条件、
既存変更の保持、禁止操作はlauncherが自動補完します。

```bash
python3 ~/.agents/skills/delegate-to-claude/scripts/delegate.py \
  --cwd /path/to/repository \
  --prompt "src/parser.pyへCSV検証を追加し、関連テストも更新する。" \
  --mode edit \
  --effort medium \
  --bash auto
```

既存変更が予定scopeと重なる、4ファイル以上の変更が見込まれる、または厳密な
acceptance criteriaが必要な場合はstrict task fileを使います。対象リポジトリの
外へ次のようなMarkdownを作成します。

```markdown
# Goal
CSV row counterのoff-by-oneを修正する。

# Allowed scope
`src/csv_counter.py` とその焦点を絞ったテストだけ。

# Acceptance criteria
空、1行、複数行の入力で期待した件数を返す。

# Required checks
`python3 -m unittest tests.test_csv_counter` を実行する。

# Existing user changes to preserve
Gitが報告する既存変更を全て保持する。

# Forbidden actions
依存関係変更、ネットワーク、commit、push、無関係なcleanupは禁止。
```

workerを実行します。

```bash
python3 ~/.agents/skills/delegate-to-claude/scripts/delegate.py \
  --cwd /path/to/repository \
  --task-file /path/to/task.md \
  --mode edit \
  --effort medium \
  --bash auto
```

公開引数は次のとおりです。

- `--cwd`: 対象Gitリポジトリ内のパス。workerはリポジトリrootで実行される
- task入力は次のどちらか一方だけ
  - `--prompt`: 空ではない32 KiB以下の短いQuick goal
  - `--task-file`: 空ではない256 KiB以下のstrict UTF-8 Markdown brief
- `--mode review|test|edit`
- `--effort medium|high`
- `--bash never|auto|require`（default: `auto`）
- `--dry-run`: workerを起動せず設定だけ検証する

`medium` は最大12 agent turn・15分、`high` は最大24 turn・30分です。モデルは
常に最新の `sonnet` aliasです。

macOSでは、既存のSeatbelt sandboxが2つ目のClaude Code sandboxを拒否する場合が
あります。`test`と`edit`のBash policyは次のように動作します（`review`ではBashを
常に無効化します）。

| policy | terminalから直接実行 | Codex／Claude Code内のnested実行 |
| --- | --- | --- |
| `never` | 無効 | 無効 |
| `auto` | launcherのstrict sandbox内で有効 | 無効 |
| `require` | launcherのstrict sandbox内で必須 | worker自身のstrict sandbox内で必須。利用不能ならfail closed |

`require`は`review`では使用できません。監督役の申告や、検証できない外側境界だけ
では許可しません。launcher自身のworker sandboxで、repository内に限定した書き
込み、秘密情報とworker network accessの遮断、unsandboxed fallbackの禁止を必須
化します。`~/.claude/session-env`にはUUID単位のdirectoryを1つだけ作り、終了時に
削除します。このdirectoryまたはsandboxを使用できない場合（未対応のnested
Seatbeltを含む）はfail closedします。全commandとtestを監督役が独立して実行できる
場合は`auto`または`never`を使います。

## mode

| mode | 対象作業 | source編集 |
| --- | --- | --- |
| `review` | 静的コードレビュー、限定されたリポジトリ調査 | 無効。実測変更があれば失敗 |
| `test` | テスト作成・保守・ローカル検証 | 有効 |
| `edit` | 小規模実装・refactor・ローカル検証 | 有効 |

並列実行できるのは `review` workerだけです。launcherはリポジトリ単位のlockで、
重複する `test`／`edit` workerを拒否します。

## 結果形式

launcherは正規化したJSON objectを1つ出力します。

```json
{
  "status": "completed",
  "summary": "限定された修正を実装し、関連テストを確認しました。",
  "changed_files": ["src/csv_counter.py", "tests/test_csv_counter.py"],
  "tests": [
    {
      "command": "python3 -m unittest tests.test_csv_counter",
      "outcome": "passed",
      "details": "4 tests passed"
    }
  ],
  "concerns": [],
  "recommended_next_action": "監督者が差分を確認し、同じテストを再実行してください。"
}
```

`changed_files` はlauncherが取得したGit基準状態との比較から実測します。委譲前
からdirtyだったファイルを含め、workerの自己申告をそのまま信用しません。

## 安全設計

全てのworker実行で次を強制します。

- Bashを有効にする場合は必ずClaude Codeのstrict OS sandboxを使う。Bash無効の
  nested起動だけは、検出した外側agent sandbox内に留める
- launcher管理の全Bash sandboxでsandbox外への再実行と全worker network domainを
  拒否し、この境界を作れなければfail closedする
- 書き込みをリポジトリ、sandbox sessionの一時領域、launcherが実行時に作成して
  終了時に削除するUUID単位の `~/.claude/session-env` metadata領域1個に制限する
- launcher管理の全Bash sandboxで一般的なcredential fileと環境tokenへのアクセス
  を拒否し、nested Bashからは検出したcredential／token環境変数もunsetする
- slash command、nested agent、MCP、browser toolを無効化する
- Claude／Codexの再帰起動、Git変更、commit、push、publish、deployを拒否する
- `bypassPermissions`を使用しない

Codex／Claude Codeからnested起動した場合、defaultの`auto`はBashを無効にして検出
した外側agent境界を使います。`require`は別のlauncher管理strict worker sandboxを
要求し、外側境界を代用として信頼しません。macOSがnested sandboxを拒否した場合や
UUID単位のsession environmentを作れない場合、launcherはworkerを起動・採用せず
sandbox failureを返します。nested Bashへ渡すenvironment fileでは、検出した
credential／token環境変数をunsetします。

sandboxは監督の代わりではありません。edit workerはリポジトリ内で誤った変更を
行う可能性があります。採用前に、必ず監督processで差分を確認し必要なcheckを
再実行してください。

認証、rate limit、sandboxの失敗は自動再試行しません。それ以外の失敗は、より
小さなタスクへ一度だけ切り直すか、監督者が作業を引き取ります。

## receiptとprivacy

実行ごとに最小限のJSONL receiptを追記します。

- macOS: `~/Library/Caches/delegate-to-claude/runs.jsonl`
- Linux: `${XDG_CACHE_HOME:-~/.cache}/delegate-to-claude/runs.jsonl`

別の保存先には `DELEGATE_TO_CLAUDE_CACHE_DIR` を設定します。receiptにはtimestamp、
task hash、入力形式（`quick`／`strict`）、要求したBash policy、実際のBash有効状態、
sandbox source、model、effort、mode、所要時間、成否、変更ファイルpath、テストの
command／outcome、Claude Codeが返すaggregate usageだけを記録します。task本文、
source code、worker summary、test details、stdout、stderr、session IDは残しません。
cacheへ書けない場合、warningを出してworker結果はそのまま返します。

## 開発と検証

offline testを実行します。

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skill/delegate-to-claude
```

test suiteはfake Claude executableと一時Gitリポジトリを使用するため、Claudeの
利用枠を消費しません。実Claudeによるforward testは別に実行します。

## 参照仕様

- [Codex: Build skills](https://developers.openai.com/codex/skills/create-skill)
- [Claude Code: Extend Claude with skills](https://code.claude.com/docs/en/slash-commands)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Claude Code sandbox](https://code.claude.com/docs/en/sandboxing)

## License

MIT
