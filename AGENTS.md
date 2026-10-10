# Agent System Status: [CUSTOMIZED]

AetherGate v2 is a production-quality, self-hosted, OpenAI-compatible inference gateway.
The scaffold has been customized; the Bootstrapper workflow is complete. Operate as a
normal Manager or Coder under the rules below.

## Mandatory reading (before any task)

1. `project_spec.md` — the single source of truth for scope, requirements, and conventions.
2. `docs/architecture/` — the v2 architecture model (living documents).
3. `docs/development/agent-workstreams.md` — module ownership and coordination rules.
4. `docs/migrations/v1-to-v2.md` — migration direction and constraints.
5. `.crules/modes/GIT_POLICY.md` — commit/branch/secret policy.

`docs/audits/v2-architecture-audit-2026-10-03.md` is the dated, immutable baseline; read it
for evidence, never edit it.

## AetherGate v2 principles

These override any generic boilerplate below when they conflict.

- Read `project_spec.md` before starting; it is authoritative.
- Respect module ownership. Do not edit another workstream's domain without coordination.
- Do not silently change public contracts (OpenAI or admin API). Contracts are owned by the
  `contracts` workstream and change through the integrator.
- Add or update tests with every implementation change; run the project's standard test command.
- Security-sensitive shortcuts are prohibited. Never weaken auth, secrets handling, or egress
  controls to ship faster.
- Provider limits must never be bypassed for throughput. Hidden adapter/SDK retries are disabled
  or routed back through admission.
- OpenAI compatibility behavior is tested with official SDK clients, not assumed.
- Migrations are explicit and reversible where practical; they run against a copy of the real
  old schema and preserve account identity and usage attribution.
- No direct production database manipulation by normal administrative tooling. The CLI and web
  console share the same `/admin/v1` API/service contracts.
- Do not commit credentials, provider secrets, `.env`, or private keys. Run the secret scan
  before committing.
- Do not perform unrelated cleanup inside a scoped task. Touch only what the task requires.
- Distinguish settled requirements from current direction and deferred decisions as marked in
  `project_spec.md`; do not promote deferred decisions to settled.

## Session lessons (persisted — read on every startup)

Hard-won, recurring mistakes. Do not repeat these.

- **Use the canonical test command; never invent container invocations.** Backend tests run through
  `scripts/dev/v2 test` (it attaches the right compose network and injects the correct
  `AETHERGATE_TEST_DATABASE_URL`). Ad-hoc `docker run` / `docker compose run` forms either fail on
  DNS (the DB container is not on the compose network) or omit `POSTGRES_*`, which produces *spurious
  failures* (3 tests failed once for exactly this reason) and wastes a full run. If you must run
  pytest directly, replicate every `POSTGRES_*`/`AETHERGATE_QUEUE_KEY`/`AETHERGATE_BOOTSTRAP_TOKEN`
  env var the compose service sets.
- **Every failing test is a real defect until root-caused.** Do not label a failure "pre-existing",
  "flaky", or "transient" to move on — the task explicitly forbids claiming completion over a red
  suite. Re-run, isolate the spec, and read the Playwright `error-context.md`/`trace` before deciding.
  A "transient" login failure after recreating containers is usually a stale nginx→api DNS or IdP/JWKS
  cache — restart the `web` container so nginx re-resolves `api`, then confirm; do not just re-run and
  hope.
- **Live E2E data accumulates across runs, and list pages sort oldest-first at 20 per page.** Any
  assertion of the form "the row I just created is visible" is a latent defect once that table exceeds
  one page. Audit *every* such assertion, not only the ones the task names. Fix with real UI
  pagination (`goToLastPage`) or a UI filter scoped to the run's unique resource. In AGV2-022C this bit
  three tables (projects, providers, price policies) and only the first two were named in the task.
- **Rebuild the image and restart the affected container before browser/E2E work.** Source edits do not
  take effect until `docker build -t aethergate-v2:local -f deploy/v2/Dockerfile .` and the api/worker
  containers are recreated from the new image; a reused `web` container also needs a restart for nginx
  DNS.

## Long-task / compaction recovery protocol

These rules are authoritative for work launched from `docs/development/current-task.md` and override
generic interactive workflow boilerplate below.

- `docs/development/current-task.md` is the canonical active assignment.
- **WIP marker protocol:** for every task launched from `current-task.md`, the first local repository
  action after entering the repo is to create the root file `.aethergate-wip`. Keep it present for
  the entire active task. It is an operator-visible local marker only: it is gitignored, must never be
  staged/committed/pushed, and must contain no secrets. Safe contents are the task ID, branch, and a
  start timestamp. If context is compacted/restarted and the task is still incomplete, recreate the
  marker if it is missing. Remove `.aethergate-wip` only after all task completion criteria are green,
  the handoff is updated, all required commits are pushed to `origin/v2`, and the remote branch has
  been verified. If the task is blocked or incomplete, leave the marker in place.
- `docs/development/agent-handoff.md` is the canonical previous-task state.
- After any context compaction, summarization, restart, or uncertainty about what remains to do,
  immediately re-read:
  1. `AGENTS.md`
  2. `project_spec.md`
  3. `docs/development/current-task.md`
  4. `docs/development/agent-handoff.md`
  Then inspect `git status` / recent history and continue the task from repo state.
- Do not ask the user to choose among commit/push/stop options when the active task already specifies
  the required completion behavior.
- If the active task says to commit and push to `origin/v2`, do that automatically after required
  verification succeeds. Never push directly to `main`.
- Do not invoke the generic `commit` shortcut/version-bump workflow merely because a scoped v2 task
  is ready to commit. Follow the active task's explicit conventional-commit instructions instead.
- Before asking the user a question, re-read the active task. If the answer or safe default is already
  specified there, continue without interrupting the user.
- If verification is incomplete, do not present completion choices. Continue verification. If a
  genuine blocker remains after best effort, record it in `agent-handoff.md`, commit/push the
  truthful state if the task permits, and stop.
- A task is complete only when its stated completion criteria are satisfied, the handoff is updated,
  commits are created, and required pushes are present on `origin/v2`.

## Common Principles
- Don't assume. Don't hide confusion. Surface tradeoffs.
- Minimum code that solves the problem. Nothing speculative.
- Touch only what you must. Clean up only your own mess.
- Define success criteria. Loop until verified.

## Note on the auto-managed section

The `crules` block between the markers below is generated/maintained by the `crules` CLI and
contains generic, multi-language boilerplate (including a "tools.py web search" snippet and a
"do not run terminal commands" note that do not describe AetherGate). Treat the AetherGate v2
principles and `project_spec.md` above as authoritative; the boilerplate applies only where it
does not conflict.

<!-- crules:rules:begin v0.20.0 -->
# Cursor Rules

Please do not run any terminal commands to run files.
however you can run terminal commands to run functions as instructed below

you have tools as functions you can use in the tools.py file

current tools are:
- web search (use this anytime you need any additional information)

you are not allowed to create .py scripts to use these functions just run the tools with a terminal command by importing and running them with a parameter in the terminal as python -c "import tools; tools.function_name(parameter)"

EXAMPLE:

## Important Files and Their Use:
- **project_spec.md** - A comprehensive document detailing the project's objectives, scope, requirements, and functionalities
- **CHANGELOG.md** - Records all version changes and updates
- **ainotes.md** - A scratch pad for the AI to document observations, ideas, and insights related to the project.

## Core Principles
1. Follow consistent code formatting and style guidelines
2. Write clear and descriptive variable and function names
3. Include appropriate comments and documentation
4. Handle errors and edge cases appropriately
5. Write modular and reusable code
6. Follow version control best practices
7. Implement proper testing
8. Consider performance implications
9. Maintain security best practices
10. Keep code DRY (Don't Repeat Yourself)

## Project Structure
- Maintain clear project structure with separate directories for:
  - src/ (source code)
  - tests/ (unit and integration tests)
  - docs/ (documentation)
  - config/ (configuration files)
- Use modular design with distinct files for:
  - models
  - services
  - controllers
  - utilities

## Development Guidelines
### Code Quality
- Use type hints consistently
- Write comprehensive docstrings (Google style)
- Follow language-specific style guides (e.g., PEP 8 for Python)
- Implement error handling with proper context
- Add logging for debugging and monitoring
- Write unit tests for new functionality
- Maintain test coverage targets

### Documentation
- Keep README.md current with setup and usage instructions
- Document API endpoints and interfaces
- Include examples for complex functionality
- Update CHANGELOG.md for version changes
- Use docstrings for all public functions and classes

### Version Control
- Follow conventional commits format:
  ```
  <type>[optional scope]: <description>
  
  [optional body]
  [optional footer(s)]
  ```
- Types: feat, fix, docs, style, refactor, test, chore
- Keep commits focused and atomic
- Write clear commit messages (imperative mood)
- Reference issues in commits when applicable

### Project Management
- Track tasks and issues in project management system
- Update project status regularly
- Document decisions and their rationale
- Follow defined release process

## File Management
### Important Files
- **project_spec.md**: Project objectives, scope, requirements
- **CHANGELOG.md**: Version changes and updates
- **README.md**: Project overview and setup instructions
- **requirements.txt/pyproject.toml**: Dependencies
- **.gitignore**: Version control exclusions

### Release Process
1. Update version numbers in relevant files
2. Update CHANGELOG.md with new version:
   ```markdown
   ## [VERSION] - YYYY-MM-DD
   ### Added
   - New features
   ### Changed
   - Modified features
   ### Fixed
   - Bug fixes
   ### Deprecated
   - Soon-to-be removed features
   ### Removed
   - Removed features
   ```
3. Create and push version tag
4. Update documentation
5. Create release notes

## Best Practices
### Security
- Never commit sensitive data (API keys, credentials)
- Use environment variables for configuration
- Implement proper authentication/authorization
- Follow security best practices for dependencies

### Performance
- Profile code for bottlenecks
- Optimize database queries
- Use appropriate data structures
- Consider scalability in design decisions

### Testing
- Write unit tests for new code
- Include integration tests for critical paths
- Maintain high test coverage
- Use test-driven development when appropriate

### Code Review
- Review all code changes
- Use pull requests for significant changes
- Provide constructive feedback
- Check for security implications

## AI Integration Guidelines
- Use descriptive variable and function names
- Add context in comments for complex logic
- Provide rich error context for debugging
- Document assumptions and edge cases
- Use type hints for better code understanding

## Maintenance
- Keep dependencies updated
- Remove deprecated code
- Refactor when complexity increases
- Monitor and address technical debt
- Keep documentation current

## Continuous Integration
- Automate builds and tests
- Run linters and formatters
- Check test coverage
- Verify documentation builds
- Deploy to staging environments

Remember to adapt these guidelines based on project-specific requirements and team preferences.

## Command Shortcuts
The following `crules` CLI commands are available for managing this project's AI context:
- `crules --setup` (`-s`): Initialize or update the global `~/.config/crules/` directory with default rules, language templates, and workflow modes.
- `crules --bootstrap` (`-b`): Deploy the Swarm infrastructure (`.crules/` directory, task pipeline, personas, and `project_spec.md`) into the current repository.
- `crules --sync` (`-S`): Refresh local `.crules/modes/` from the global workflow templates and re-deploy rules to all IDE rule folders.
- `crules --refresh-defaults` (`-R`): Overwrite the global `~/.config/crules/cursorrules` file with the packaged `default_cursorrules`, without touching workflows or language rule templates.
- `crules --status`: Print a diagnostic report of the global crules configuration and the current project, including missing pieces and suggested commands to fix them.
- `crules --list` (`-l`): List all available language rule files.
- `crules <lang> [<lang> ...]`: Compile language-specific rules for all enabled AI assistants (e.g., `crules python bash`).
- `crules --target <tool> <lang>` (`-t`): Limit rule generation to specific assistants (e.g., `crules -t cursor -t claude python`).
- `crules --force` (`-f`): Force overwrite of existing files during setup or rule generation.
- `crules --legacy`: Generate a single `.cursorrules` file instead of per-IDE directories.
- `crules --verbose` (`-v`): Enable detailed debug logging.

## Shortcut Commands
When the user types one of these keywords, activate the described behaviour:
- **commit**: Act as Manager. Read `.crules/modes/GIT_POLICY.md`. Then:
  1. Run a heuristic secret scan (file-name and content regex checks from GIT_POLICY) on all staged changes. If secrets are detected, block the commit and report findings.
  2. Check for modified but unstaged `.crules/` files (modes, tasks) and `project_spec.md`. If any are found, stage them automatically and inform the user which files were added.
  3. **Version Initialization Guard**: Before any version bump, verify that a `__version__` string exists in the package's `__init__.py` and a `version` field exists in `pyproject.toml`. If either is missing, STOP and prompt the user: "No version string found in [file]. Initialize at 0.1.0?" Only proceed after the user confirms and the version is written to all expected locations.
  4. Triangulate the highest current version: check `pyproject.toml`, `src/crules/__init__.py` (`__version__`), and Git tags. The highest value found is your base version.
  5. **Detect Change Type**: Inspect `git diff --cached --diff-filter=A --name-only` and `git diff --cached` for newly added files, classes, or function definitions. If new files or functions are present, the commit type is `feat` and the bump MUST be at least `minor`, regardless of what the user requested.
  6. Determine the version bump: if the user explicitly said "minor" or "major", use that level; otherwise default to "patch". Follow GIT_POLICY rules (feat -> minor, fix/chore -> patch). The Detect Change Type rule above overrides: if new files/functions are staged, the floor is `minor`.
  7. Apply the SemVer bump to the **highest** version found (never a lower one).
  8. **Metadata Sync**: Write the new version into both `pyproject.toml` and `src/<pkg>/__init__.py` **before** running `git add`. Both files must contain the identical new version string.
  9. Stage `pyproject.toml`, `src/<pkg>/__init__.py`, and all other relevant files.
  10. **Version Validation**: Before the final `git commit`, verify the version using `python3 -m <pkg> --version` to avoid needing a global reinstall during the commit process. Capture the output.
  11. **Abort on Mismatch**: If the CLI version output does not exactly match the new metadata version, STOP. Do NOT commit. Report the discrepancy and fix the code so the runtime version matches the metadata before retrying.
- **branch**: Act as Manager. Read `.crules/modes/GIT_POLICY.md`. Create a new branch following the GIT_POLICY naming convention (`feat/`, `fix/`, `docs/`, `chore/`, `refactor/`) based on the current task description.
- **release**: Act as Manager.
  a. **Verify**: Run `python3 -m <pkg_name> --version` to ensure it matches pyproject.toml.
  b. **Changelog**: Summarize all 'feat' and 'fix' commits since the last git tag.
  c. **Tag**: Create a git tag for the current version (e.g., v0.5.1).
  d. **Push**: Execute `git push origin main --tags`.
  e. **Announce**: Provide a summary of the release for a GitHub Release description.

## Universal Agent System
- **Context Discovery**: Upon first run, you must read `.crules/modes/` to determine your current active persona (Manager or Coder).
- **State Management**: Reference `summary.txt` and `instructions.txt` for cross-session continuity.
- **Bootstrap Protocol**: If `project_spec.md` is in "EVALUATION REQUIRED" status, you must immediately adopt the Manager persona, scan the repo (languages, frameworks, configs), and rewrite the `project_spec.md` to reflect the actual codebase.
- **Tasking**: All work must be tracked as Markdown files in `.crules/tasks/wip/` before implementation begins.
- **Version Control Integrity**: The `.crules/` directory and `project_spec.md` are integral to the project's identity and MUST be tracked in Git. Do not add them to `.gitignore`.
# --- Delimiter ---
## Language rules: python

You are an AI assistant specialized in Python development. Follow these guidelines:

1. Adhere to PEP 8 Style Guide:
- Ensure consistent indentation, naming conventions, and code layout.
- Limit lines to 79 characters for code and 72 for docstrings.
2. Use f-Strings for String Formatting:
- Modern and concise way to format strings.
- Example: f"The answer is {x}"
3. Incorporate Type Hints:
- Specify types for function parameters and return values.
- Example: def add(a: int, b: int) -> int:
4. Write Docstrings in Google Style:
- Provide clear descriptions for classes, methods, and functions.
- Include parameters, return values, and exceptions if any.
5. Utilize List Comprehensions and Generator Expressions:
- Make code more readable and concise.
- Use generators for memory-efficient iteration.
6. Use 'with' Statements for Resource Management:
- Automatically handle opening and closing of files or resources.
- Example: with open('file.txt') as f:
7. Set Up Virtual Environments:
- Isolate project dependencies.
- Use pipx to create virtual environments.
8. Follow the Zen of Python:
- Prioritize readability, simplicity, and explicitness in code.
9. Use snake_case for Variables and Functions, PascalCase for Classes:
- Maintain consistency with naming conventions.
10. Implement Error Handling with try-except Blocks:
- Handle exceptions where necessary to prevent crashes.
- Avoid broad exceptions; be specific.
11. Leverage Built-in Functions and Standard Library Modules:
- Use modules like os, sys, collections, etc., for common tasks.
12. Prefer Context Managers Over try-finally:
- Simplify resource management with with statements.
13. Use Comprehensions for Concise Code:
- Ensure that code remains readable and not overly complex.
14. Follow the Liskov Substitution Principle:
- Ensure that subclasses can replace their base classes without issues.
15. Use 'is' Instead of '==' When Appropriate:
- Check for identity rather than equality for singletons like None.
16. Utilize zip, enumerate, and Tuple Unpacking:
- Simplify loops and iterations over data structures.
17. Use the logging Module for Debugging:
- Configure logging levels and formats for better traceability.
18. Avoid Using shell=True in subprocess Calls:
- Prevent security risks associated with shell injection.
19. Use NumPy for Array Operations:
- Leverage NumPy's efficient array handling for numerical computations.
20. Utilize Sphinx to generate documentation from Python docstrings, integrating it into the docs/ directory and employing the Napoleon extension for Google-style docstrings. Automate documentation builds with a CI/CD pipeline.
21. Incorporate Mermaid for creating diagrams, using the sphinxcontrib-mermaid extension in Sphinx documentation. Ensure diagrams are current and clarify complex processes in the system architecture.

Project structure should be setup as follows:
my_project/
├── README.md
├── LICENSE
├── requirements.txt
├── setup.cfg
├── pyproject.toml
├── .gitignore
├── .pre-commit-config.yaml
├── src/
│   └── my_package/
│       ├── __init__.py
│       ├── module1.py
│       ├── module2.py
│       └── subpackage/
│           ├── __init__.py
│           └── submodule.py
├── tests/
│   └── test_module1.py
│   └── test_module2.py
├── docs/
│   └── conf.py
│   └── index.rst
└── scripts/
    └── run_script.py

Explanation of Project Structure
README.md: Project description, installation instructions, and usage guidelines.
LICENSE: License file defining the terms of use for the project.
requirements.txt: Lists project dependencies for reproducibility.
setup.cfg and pyproject.toml: Configuration files for setuptools and build system.
.gitignore: Specifies files and directories to be ignored by Git.
.pre-commit-config.yaml: Configuration for pre-commit hooks to enforce code quality.
src/: Contains the source code organized into packages and modules.
my_package/: The main package with submodules and subpackages.
tests/: Holds unit tests for the codebase.
docs/: Documentation files using Sphinx or another documentation generator.
scripts/: Scripts for running the application or other tasks.
# --- Delimiter ---
## Available Skills

Skills are reusable instruction sets (open Agent Skills standard). If your tool does not load them natively, read the referenced SKILL.md before performing a matching task.

- **commit** — Version-guarded commit protocol — secret scan, SemVer triangulation, metadata sync, runtime validation — see `.agents/skills/commit/SKILL.md`
- **context-resume** — Generate a Context Resume for handing work off to a new session or a different AI tool — see `.agents/skills/context-resume/SKILL.md`
- **release** — Release protocol — verify version, changelog from commits, tag, push, announce — see `.agents/skills/release/SKILL.md`
- **spec-phase** — Spec-driven, phase-bounded development — implement exactly one approved phase, verify, stop — see `.agents/skills/spec-phase/SKILL.md`
- **start-project** — Scaffold a new project to standard defaults and converge it — one command from idea to working skeleton — see `.agents/skills/start-project/SKILL.md`
<!-- crules:rules:end -->
