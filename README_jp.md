# delegate-to-claude

[English](README.md)

`delegate-to-claude` は、Codex または Claude Code を監督者として維持した
まま、明確に限定されたリポジトリ作業を新しい Claude Code Sonnet worker
へ委譲する、両環境共通の Agent Skill です。

worker は小規模な実装、テスト作成・実行、失敗原因の調査、コードレビューを
担当できます。タスクの切り分け、差分レビュー、独立した再検証、最終承認は
常に監督者が担当します。

## 目的

上位モデルはアーキテクチャ、曖昧な判断、最終レビューでは有用ですが、機械的な
工程まで全て担当させるとトークンやレートリミットを多く消費します。この Skill
は、次の条件を満たす作業だけを委譲します。

- スコープを正確に説明できる
- ローカルかつ可逆である
- 監督者が自分で行うより、結果を検証する方が安い
- 差分、テスト、焦点を絞ったレビューなど客観的な確認方法がある

アーキテクチャ、プロダクト判断、セキュリティ上重要な変更、大規模移行、秘密
情報、外部副作用、最終承認は監督者に残します。

## 必要環境

- macOS、または Claude Code sandbox の依存関係を導入した Linux
- 認証済みの Claude Code 2.1.205 以降
- Python 3.9 以降
- Git
- Agent Skills に対応する Codex または Claude Code

launcher は既存の Claude Code ログインを使用します。APIキーの取得、表示、
保存、変更は行いません。

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

- `~/.codex/skills/delegate-to-claude`
- `~/.claude/skills/delegate-to-claude`

既存のファイル、ディレクトリ、別のsymlinkは上書きしません。初回導入後は
Codexを再起動してください。Claude Code起動時に個人skillsディレクトリが存在
しなかった場合は、Claude Codeも再起動します。以後のリポジトリ更新はsymlink
経由で反映されます。

## Codexから使う

明示的にSkillを呼び出します。

```text
$delegate-to-claude を使って、限定されたparserの修正を実装しunit testを実行して。
```

タスクがdescriptionと委譲基準に一致する場合、Codexが自動的にSkillを選択する
こともあります。

## Claude Codeから使う

同じSkillを明示的に呼び出します。

```text
/delegate-to-claude 現在の認証周りの差分にregressionがないかレビューして
```

Claude CodeもSkillを自動的に読み込む場合があります。Claude Code自身が監督者
の場合でも、Skillは別の非対話 `claude -p` processを起動し、新しいcontextと
固定されたSonnetモデルをworkerへ与えます。

## launcherを直接使う

通常は監督者がtask briefを作成してlauncherを実行します。直接試す場合は、対象
リポジトリの外へ次のようなMarkdownを作成します。

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
python3 ~/.codex/skills/delegate-to-claude/scripts/delegate.py \
  --cwd /path/to/repository \
  --task-file /path/to/task.md \
  --mode edit \
  --effort medium
```

公開引数は次のとおりです。

- `--cwd`: 対象Gitリポジトリ内のパス。workerはリポジトリrootで実行される
- `--task-file`: 空ではない256 KiB以下のUTF-8 Markdown brief
- `--mode review|test|edit`
- `--effort medium|high`
- `--dry-run`: workerを起動せず設定だけ検証する

`medium` は最大12 agent turn・15分、`high` は最大24 turn・30分です。モデルは
常に最新の `sonnet` aliasです。

macOSでは、既存のCodex／Claude Code sandbox内へClaude CodeのSeatbelt sandboxを
重ねて起動できません。launcherがnested agent sessionを検出した場合は外側sandbox
を継承し、workerのtool setからBashを完全に除外します。workerは限定されたfile
toolでreviewや編集を行えますが、全commandとtestは監督者が独立して実行します。
通常terminalから直接起動した場合はClaude Codeのstrict sandboxを使い、ローカル
checkもworkerが実行できます。

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

- terminalからの直接起動ではClaude Codeのstrict OS sandbox、nested起動では検出
  した外側agent sandboxを使い、どちらの境界も利用できなければfail closedする
- sandbox外への再実行と全network domainを拒否する
- 書き込みをリポジトリ、sandbox sessionの一時領域、launcherが実行時に作成して
  終了時に削除するUUID単位の `~/.claude/session-env` metadata領域1個に制限する
- 一般的なcredential fileと環境tokenへのアクセスを拒否する
- slash command、nested agent、MCP、browser toolを無効化する
- Claude／Codexの再帰起動、Git変更、commit、push、publish、deployを拒否する
- `bypassPermissions`を使用しない

Codex／Claude Codeからnested起動した場合は、検証済みの外側agent sandboxが内側
Claude sandboxを置き換え、workerはBashを利用できません。これにより、未sandbox
shellへの危険なfallbackをせず、macOSで非対応のnested Seatbeltを回避します。

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
task hash、model、effort、mode、所要時間、成否、変更ファイルpath、テストの
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
