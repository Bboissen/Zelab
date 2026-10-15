# Where the team's rules come from

The personas in this folder turn what senior practitioners report about AI coding agents
into working rules. Research was done in October 2026. Reddit could not be fetched directly;
the PM and game threads were read through the Arctic Shift archive, and elsewhere Reddit
appears only where press or Hacker News quoted it. X was seen only in search snippets. Each list keeps the
strongest sources; a rule seen in a single source is stated more cautiously.

## Shared (TEAM.md)

- Anthropic, Claude Code best practices and agent teams: https://code.claude.com/docs/en/best-practices.md, https://code.claude.com/docs/en/agent-teams.md
- Simon Willison on vibe engineering: https://simonwillison.net/2025/Oct/7/vibe-engineering/
- METR, experienced developers 19% slower with AI: https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/
- MAST, why multi-agent systems fail: https://arxiv.org/html/2503.13657v3
- Cognition, "Don't build multi-agents": https://cognition.com/blog/dont-build-multi-agents
- Birgitta Böckeler (Thoughtworks): https://martinfowler.com/articles/exploring-gen-ai/13-role-of-developer-skills.html
- Sean Goedecke on agents and code review: https://www.seangoedecke.com/ai-agents-and-code-review
- GitClear duplication data: https://devclass.com/2025/02/20/ai-is-eroding-code-quality-states-new-in-depth-report/
- Veracode, insecure code in 45% of tasks: https://www.veracode.com/press-release/ai-generated-code-poses-major-security-risks-in-nearly-half-of-all-development-tasks-veracode-research-reveals/
- Hacker News: https://news.ycombinator.com/item?id=44885398, https://news.ycombinator.com/item?id=45648422, https://news.ycombinator.com/item?id=47357042

## Product manager

- r/ProductManagement threads on invented evidence, empty PRDs, build vs buy and owning what you send:
  https://www.reddit.com/r/ProductManagement/comments/1r0vhfc/, https://www.reddit.com/r/ProductManagement/comments/1l3bpcx/,
  https://www.reddit.com/r/ProductManagement/comments/1pxji99/, https://www.reddit.com/r/ProductManagement/comments/1v2r2zt/,
  https://www.reddit.com/r/ProductManagement/comments/1rlqthk/, https://www.reddit.com/r/ProductManagement/comments/1rkln1w/
- r/ExperiencedDevs on PMs who use AI for everything: https://www.reddit.com/r/ExperiencedDevs/comments/1skp7ch/
- Shreyas Doshi on AI strategy documents (snippet): https://x.com/shreyas/status/1938934124095373347
- A beautiful wrong draft gets approved: https://dev.to/fathyshalaby/ai-is-writing-your-prds-whos-checking-if-theyre-right-37b9
- NN/G on synthetic users (secondary): https://radar.zurb.com/article/nielsen-norman-synthetic-users-were-too-shallow-to-be-useful-against-three-real-studies
- Teresa Torres and Petra Wille: https://www.producttalk.org/ai-generated-opportunity-solution-trees-all-things-product-podcast-with-teresa-torres-petra-wille/
- Workslop (secondary): https://fortune.com/2025/11/12/mit-researcher-gives-advice-tame-harness-ai-workslop

## Game developer

- r/gamedev on agents in game code: https://www.reddit.com/r/gamedev/comments/1twjm0m/, https://www.reddit.com/r/gamedev/comments/1n36kme/,
  https://www.reddit.com/r/gamedev/comments/1r3jeb7/, https://www.reddit.com/r/gamedev/comments/1x0hay1/
- r/godot on shader and vector maths: https://www.reddit.com/r/godot/comments/1lztbkq/
- Godot forum on Godot 3 and 4 confusion: https://forum.godotengine.org/t/is-it-bad-to-use-chatgpt-to-help-me-learn-specific-situations-in-godot/121995
- HN on Godogen and AI harnesses for games: https://news.ycombinator.com/item?id=47400868, https://news.ycombinator.com/item?id=50072120, https://news.ycombinator.com/item?id=49550375
- Kyle Kukshtel, AI harness engineering for game development: https://kylekukshtel.com/ai-harness-engineering-game-development-claude-code
- GDC 2026 survey: https://www.techspot.com/news/111128-more-than-50-game-developers-generative-ai-harming.html

## Frontend

- Adam Argyle, "Why AI sucks at front end", and its HN thread: https://nerdy.dev/why-ai-sucks-at-front-end, https://news.ycombinator.com/item?id=47738864
- Adrian Roselli's accordion test (via Silktide): https://silktide.com/blog/google-antigravity-accessibility-issues/
- AI-generated UI is inaccessible by default: https://blog.master.dev/ai-generated-ui-is-inaccessible-by-default/
- Design-system drift: https://overlayqa.com/blog/ai-design-system-drift/
- Thomas Findlay, vibe coding against production React: https://theroadtoenterprise.com/blog/vibe-coding-vs-production-coding-react
- Josh Comeau: https://www.joshwcomeau.com/email/wham-launch-005-elephant-2-p/
- HN: https://news.ycombinator.com/item?id=48321631

## Mobile

- Paul Hudson, what to fix in AI-generated Swift: https://www.hackingwithswift.com/articles/281/what-to-fix-in-ai-generated-swift-code, https://github.com/twostraws/swiftui-agent-skill
- Swift 6 data races silenced, not fixed: https://dev.to/jbrackin/watch-a-coding-agent-silence-a-swift-6-data-race-instead-of-fixing-it-4jpp
- Claude Code for iOS: https://dev.to/5uper0/claude-code-for-ios-app-development-the-workflow-not-the-setup-5bfo
- Android: https://dev.to/vikas_sahani_3a7e2706846c/why-i-stopped-trusting-ai-to-write-my-android-code-and-what-i-built-instead-369j, https://dev.to/artemgarazha/vibe-coding-on-android-a-chronicle-of-problems-nobody-asked-for-1bba
- Google, Android skills for agents: https://android-developers.googleblog.com/2026/04/build-android-apps-3x-faster-using-any-agent.html
- Callstack, React Native best practices for agents: https://www.callstack.com/blog/announcing-react-native-best-practices-for-ai-agents
- Flutter review: https://dev.to/nixan/how-to-review-ai-generated-flutter-code-before-it-breaks-production-486f
- Taylor Arndt, testing vibe-coded apps with VoiceOver: https://taylorarndt.substack.com/p/i-keep-testing-vibe-coded-apps-here

## Backend

- Karpathy and HN on swallowed exceptions: https://news.ycombinator.com/item?id=45530486
- DoltHub, Claude Code gotchas: https://www.dolthub.com/blog/2025-06-30-claude-code-gotchas/
- Kent Beck: https://tidyfirst.substack.com/p/augmented-coding-beyond-the-vibes
- Postgres migration locks: https://dev.to/yureki_lab/how-i-ran-a-zero-downtime-postgres-migration-on-a-180m-row-table-with-claude-code-10k2, https://dev.to/mickelsamuel/your-ai-agent-writes-migrations-that-look-safe-heres-what-they-actually-do-to-postgres-27a7
- Armin Ronacher, agentic coding: https://lucumr.pocoo.org/2025/6/12/agentic-coding/
- antirez: https://antirez.com/news/154
- Tom Wojcik: https://tomwojcik.com/posts/2026-02-15/finding-the-right-amount-of-ai/
- Text-to-SQL failure modes: https://tianpan.co/blog/2026/04/10/text-to-sql-failure-modes-production

## Data science

- CWI, silent failures in agentic data science: https://arxiv.org/abs/2610.14115
- Andrey Lukyanenko on agents in a Kaggle competition: https://andlukyane.com/blog/rogii-geosteering-kaggle
- AWS, competing on Kaggle in the generative-AI era: https://builder.aws.com/content/3GPG0ESURRFojzYvMfSMLuHTRhh/how-to-compete-on-kaggle-in-the-generative-ai-era
- MLE-bench: https://arxiv.org/html/2410.07095v6
- Claude for data analysis: https://agentscamp.com/guides/analytics/claude-for-data-analysis
- Richie Manandhar: https://richiemanandhar.substack.com/p/claude-did-weeks-of-work-and-still
- Karthik Shashidhar: https://artofdatascience.substack.com/p/finally-a-good-coding-agent-for-data
- scikit-learn common pitfalls: https://scikit-learn.org/stable/common_pitfalls.html

## DevOps

- DataTalks.Club Terraform destroy, postmortem and HN: https://aishippingblog.com/p/how-i-dropped-our-production-database, https://news.ycombinator.com/item?id=47275157, https://news.ycombinator.com/item?id=47278720
- PocketOS volume delete: https://news.ycombinator.com/item?id=47911524
- `docker compose down -v` data loss: https://claudeissues.com/issue/63644-claude-ran-docker-compose-down-v-without-user-permission-destroying-database-vol
- Google SRE, AI engineering for reliable operations: https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/
- AI-generated IaC failure modes: https://tianpan.co/blog/2026/05/06/ai-generated-iac-terraform-kubernetes-failure-modes
- Prisma, stop your agent dropping your database: https://www.prisma.io/blog/stop-your-ai-agent-dropping-your-database
- Docker, the agent that deleted production: https://www.docker.com/blog/coding-agent-horror-stories-the-agent-that-deleted-production/
- ClickHouse, LLMs in incident investigation: https://clickhouse.com/blog/llm-observability-challenge

## Sysadmin

- `chown -R` cascade with sudo: https://claudeissues.com/issue/39283-safety-background-agent-with-sudo-caused-cascading-system-destruction-via-chown
- Claude Code for sysadmins: https://larcombe.tech/blog/claude-code-for-sysadmins.html
- Heinzel ruleset: https://github.com/wintermeyer/heinzel
- AI in a homelab: https://homelabpro.xyz/posts/2026-05-04-ai-in-my-homelab/
- Docker ports bypass UFW: https://www.ssdnodes.com/learn/docker-ports-bypass-ufw
- An agent upgraded, edited GRUB and rebooted: https://www.theregister.com/2024/10/02/ai_agent_trashes_pc/
- Linux performance analysis in 60 seconds: https://www.thomas-krenn.com/en/wiki/Linux_performance_analysis_in_60_seconds
- Synology Docker permissions: https://mariushosting.com/synology-common-docker-issues-and-fixes/
