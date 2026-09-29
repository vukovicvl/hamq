# Kako koristiti ovaj agent kit u VS Code

## Šta je gde

| Putanja | Namena |
|---|---|
| `PLAN.md` | MVP plan, faze, model podataka |
| `AGENTS.md` | Pravila za svakog agenta (arhitektura, Qt5/Qt6, komande, definicija gotovog) |
| `.github/copilot-instructions.md` | Kratko upućuje Copilot na `AGENTS.md` |
| `.github/agents/*.agent.md` | Custom agenti: planner, implementer, reviewer, tester |
| `.github/prompts/*.prompt.md` | Gotovi promptovi: `/plan-milestone`, `/do-task`, `/review-task`, `/release-check` |
| `.github/skills/*/SKILL.md` | Domensko znanje: pyqgis-plugin, maidenhead, adif, geodesy, dxcc-cty, wsjtx-udp, hamlib |
| `tasks/` | Task fajlovi, jedan po poslu. `_TEMPLATE.md` + prvi task `M0-01` |

## Tok rada

1. `/plan-milestone M0` (planner napravi task fajlove, ako M0-01 nije dovoljan)
2. `/do-task tasks/M0-01-scaffold.md` (implementer)
3. `/review-task tasks/M0-01-scaffold.md` (reviewer)
4. Ručna provera u QGIS 3.40 i 4.x, commit
5. Sledeća faza

Jedan task, jedan chat. Kad se chat produži, počni novi i daj mu samo task fajl.

## GitHub Copilot (VS Code)

Radi direktno: agenti se pojavljuju u biraču agenata, promptovi preko `/`,
skills se učitavaju iz `.github/skills/` kad opis odgovara zadatku.
Ako tvoja verzija VS Code ne vidi skills automatski, uključi Agent Skills
u podešavanjima ili u task fajlu eksplicitno navedi putanju do SKILL.md.

## Claude Code (ekstenzija ili terminal)

Claude Code čita `CLAUDE.md` i `.claude/`. Poveži postojeće fajlove:

```bash
ln -s AGENTS.md CLAUDE.md
mkdir -p .claude
ln -s ../.github/skills .claude/skills
mkdir -p .claude/agents
for f in .github/agents/*.agent.md; do
  ln -s "../../$f" ".claude/agents/$(basename "$f" .agent.md).md"
done
```

Promptove iz `.github/prompts/` možeš prebaciti u `.claude/commands/`
(isti sadržaj, bez `agent:` polja u zaglavlju).
