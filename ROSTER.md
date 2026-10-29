# ROSTER.md — 22 роли, права и вето

Источник истины — frontmatter карточек в [`factory/roles/`](factory/roles/). `factory doctor` проверяет восемь разделов, уникальность, `active_stages`, `write_scopes` и tier.

## 1. Штатное расписание

| Роль | Название | Tier | Вето | `write_scopes` | Активные стадии |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `00-orchestrator` | Orchestrator & Meta-Agent | `tier-3-frontier` | `blocking` | `factory_state, own_artifacts` | все 13 |
| `01-smart-interviewer` | Smart Interviewer & Spec Author (Nano dual role) | `tier-3-frontier` | `blocking` | `own_artifacts` | `s_in`, `s00_intake`, `s0_vision`, `s1_spec`, `s3_plan`, `s9_learn` |
| `02-independent-verifier` | Independent Verifier & Adversarial Auditor | `tier-3-frontier` | `blocking` | `own_artifacts` | все 13 |
| `03-product-owner` | Product Owner (Ponytail Value Guardian) | `tier-3-frontier` | `advisory` | `own_artifacts` | `s0_vision`, `s9_learn` |
| `04-team-lead` | Team Lead & Task Decomposer | `tier-2-balanced` | `blocking` | `own_artifacts` | `s3_plan`, `s4_build` |
| `05-business-analyst` | Business Analyst (Dual-View Spec Author) | `tier-3-frontier` | `blocking` | `own_artifacts` | `s1_spec` |
| `06-system-analyst` | System Analyst (Domain & Access Modeling) | `tier-3-frontier` | `advisory` | `own_artifacts` | `s1_spec` |
| `07-ux-ui-designer` | UX/UI Designer (5-State Screen Architect) | `tier-2-balanced` | `advisory` | `own_artifacts` | `s2_architecture` |
| `08-software-architect` | Software Architect (Ponytail Minimalist) | `tier-3-frontier` | `blocking` | `own_artifacts` | `s_in`, `s2_architecture` |
| `09-ai-engineer` | AI/LLM Engineer (Guardrails & Eval Architect) | `tier-3-frontier` | `blocking` | `code, own_artifacts` | `s2_architecture`, `s4_build`, `s5_acceptance`, `s6_security` |
| `10-frontend-developer` | Frontend Developer | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s4_build` |
| `11-backend-developer` | Backend Developer | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s4_build`, `s6_security` |
| `12-fullstack-developer` | Fullstack Developer (Primary Nano Executor) | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s2_architecture`, `s3_plan`, `s4_build`, `s5_acceptance`, `s6_security`, `s7_release`, `s8_docs` |
| `13-mobile-developer` | Mobile & Cross-Platform Developer | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s4_build` |
| `14-qa-engineer` | QA Engineer & Acceptance Gatekeeper | `tier-2-balanced` | `blocking` | `own_artifacts` | `s5_acceptance`, `s8_docs` |
| `15-qa-automation-engineer` | QA Automation Engineer (Zero-Flake Suite) | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s4_build`, `s5_acceptance` |
| `16-security-engineer` | Security Engineer & SkillSpector Gatekeeper | `tier-3-frontier` | `blocking` | `factory_state, own_artifacts` | `s15_quarantine`, `s2_architecture`, `s6_security` |
| `17-dba` | Database Administrator (Reversible Migrations Auditor) | `tier-3-frontier` | `blocking` | `own_artifacts` | `s2_architecture`, `s6_security` |
| `18-data-engineer` | Data Engineer (Idempotent Pipelines) | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s4_build`, `s6_security` |
| `19-devops-engineer` | DevOps Engineer (CI/CD & Verified Rollback Gatekeeper) | `tier-2-balanced` | `blocking` | `code, own_artifacts` | `s7_release` |
| `20-system-administrator` | System Administrator & SRE (Operations Runbook) | `tier-2-balanced` | `advisory` | `own_artifacts` | `s7_release` |
| `21-technical-writer` | Technical Writer (Adaptive Product Documentation) | `tier-2-balanced` | `advisory` | `code, own_artifacts` | `s8_docs` |

`02-independent-verifier` не имеет `code`: он не правит продукт. Записи в журнал идут только через `factory` CLI; это не прямое редактирование `state.json`/`blackbox.jsonl`. Поэтому `blackbox_logging: required` не требует `factory_state` у каждой роли.

## 2. Режимы

| Режим | Роли |
| :--- | :--- |
| `nano` | `00`, `01`, `12`, `02` |
| `standard` | `00`, `01`, `02`, `03`, `04`, `05`, `06`, `08`, `12`, `14`, `16`, `19` |
| `enterprise` | все 22 |
| `brownfield` | те же 12, что `standard` |

В `nano` роли `01` дополнительно пишет spec/spec-human/traceability/plan; роль `12` — архитектуру, код, тесты, приёмку, security, release и docs. Это компромисс, а не скрытая пятая роль. Проверяется `factory doctor`.

## 3. Матрица вето

| Тип | Роли | Правило |
| :--- | :--- | :--- |
| `blocking` | 00, 01, 02, 04, 05, 08, 09, 14, 16, 17, 19 | Останавливает стадию; обязателен код причины и evidence |
| `advisory` | 03, 06, 07, 10, 11, 12, 13, 15, 18, 20, 21 | Записывается в отчёт, не останавливает стадию |

Приоритет:

```text
человек > гейт > blocking veto > advisory veto
```

- три блокирующих вето на стадию → `escalated`, решение за человеком;
- человек снимает вето только событием `RISK_ACCEPTANCE` с риском, сроком и ответственным;
- `advisory` не может «блокировать»: формулировка карточки проверяется вручную и тестом стиля;
- вето без evidence отклоняется журналом для `FAIL`/`BLOCKED`.

## 4. Права

| Scope | Что означает |
| :--- | :--- |
| `code` | Файлы продукта: `runs/<id>/product/src`, `product/tests`, `product/docs`, `product/README.md` |
| `own_artifacts` | Артефакты своей стадии в `runs/<id>/artifacts`, `tasks/<task_id>` |
| `factory_state` | `state.json`, `blackbox.jsonl`, `skills-lock.json`, `AUDIT_TRAIL.md` — только через CLI |

Проверка перед записью:

- `Allowed Files` задачи не пересекаются с другими задачами волны;
- `product/src` и `product/tests` не пишутся вне задачи;
- `Untouchable Paths` Brownfield блокируют запись;
- шаблоны и схемы не перезаписываются прогоном.

## 5. Эскалация

| Событие | Кому |
| :--- | :--- |
| 3 отказа задачи | `04-team-lead` → `00-orchestrator` → человек |
| 3 отказа гейта | `00-orchestrator` → человек |
| `CAUTION` от SkillSpector | `16-security-engineer` → человек |
| `restore-drill` не `PASS` | `01-smart-interviewer` → человек |
| Rollback drill не пройден | `19-devops-engineer` → человек |
| Конфликт требований | `05-business-analyst` → `00-orchestrator` → человек |
