# BOSTER — PHASE KV CACHE 1
# RAPPORT D'AUDIT EN LECTURE SEULE

`HEAD` : `bf9134e336e6605bc37955e7cb5db4d84bbe998f` (« v2.2-2.5: deterministic pipeline, structured memory, memory validation »)
Audit réalisé sans modification : aucun fichier suivi modifié, `git diff --name-only` vide, `pyproject.toml` et `uv.lock` intacts, aucun commit, aucun paramètre moteur changé.

> Règle de lecture du rapport : chaque affirmation porte un niveau explicite.
> **[DOC]** = comportement documenté par le moteur/projet · **[OBS]** = observé localement sur ce poste · **[NON VÉRIFIÉ]** = non établi.
> Aucune similarité textuelle n'est présentée comme un cache hit. Aucun compteur réutilisé n'est inventé.

---

## 0. Résumé exécutif

Boster économise des tokens (objectif 1) mais **dégrade la réutilisation du préfixe** (objectif 2) dès qu'il agit.

Le fait central, mesuré : le bloc de contexte est injecté **dans le system prompt** — donc dans le préfixe — et il est **reconstruit à chaque tour en fonction de la question du tour**.

| Mesure | Valeur | Où |
|---|---|---|
| Tours où le pipeline agit (n=100, budget 2 000) | **38 / 100** | §5 |
| Coupures de préfixe tombant **dans le bloc** | **38 / 38** | §4 |
| Part du bloc réutilisable d'un tour à l'autre (régime stable) | **26 %** (1 150 c / ~4 400 c) | §4.2 |
| Part du bloc réutilisable quand la section d'ouverture change | **2,6 %** (113 c) | §4.3 |
| Part du bloc réutilisable quand **seule la question** change | **0,4 % – 4,5 %** (17–199 c) | §4.4 |
| Préfixe stable sur 100 tours | **219 c** ≈ les seules instructions | §4.1 |
| Compteurs de réutilisation exposés par le moteur | **aucun** | §8 |
| Écart estimation `chars/4` ↔ comptage moteur | **×1,87** (2 360 vs 1 262) | §7.3 |
| Préfill à recalculer par tour | **inchangé** quel que soit l'agencement (±12 %) | §7.1 |

Conclusion : **quand Boster n'agit pas, il est parfaitement compatible avec un cache de préfixe. Quand il agit, il invalide le préfixe à chaque tour** — et comme le bloc réécrit pèse 84 % du prompt, le préfill par tour reste le même : les tokens économisés sont exactement ceux qui auraient été mis en cache. L'objectif 2 échoue précisément dans le régime qui justifie Boster.

---

## 1. Architecture actuelle

Point d'entrée unique : `run_deterministic_pipeline()` — `boster/deterministic_pipeline.py:1279`
Signature : `(messages, *, system_prompt="", query="", config: PipelineConfig)`

| Étape | Fonction | Ligne |
|---|---|---|
| Analyse entrée | `analyze_input()` | 329 |
| Construction mémoire | `build_memory()` | 500 (appel 1343) |
| Sélection chronologique | `selected_sorted = sorted(selected, key=…index)` | 1388 |
| Allowance mémoire | `memory_allowance = int(cfg.max_tokens * BUDGET_LEVELS[cfg.level])` | 1423 |
| Construction du bloc | `build_context_block()` | 757 (appel 1429) |
| Assemblage | `system_sections` → `final_system` | 1436-1443 |
| Messages finaux | `final_messages` + `KEEP_RECENT_BY_LEVEL` | 1449-1455 (`KEEP_RECENT_BY_LEVEL` défini l. 145) |
| Candidats | tuple `original / summarized / reconstructed` | 1476 |
| Choix | `chosen` | 1515 |

`KEEP_RECENT_BY_LEVEL` = `{minimal: 2, balanced: 4, complete: 8}` (l. 145).

Le bloc est construit **sous une allowance** (`build_context_block`, docstring l. 766-771) : « Without an allowance the block grows with the conversation — measured: … the memory never engaged at all. With an allowance the sections … ». Le mécanisme d'allowance est donc ce qui fait *agir* la mémoire — et c'est aussi ce qui rend le bloc volatil.

## 2. Flux de construction du prompt

Les dix points demandés, avec preuve.

| # | Question | Constat | Preuve |
|---|---|---|---|
| 1 | Comment le prompt est construit | `system_prompt` = [system appelant] + `[DEVELOPER]` + **bloc** joints par `"\n\n"`, puis `final_messages` | `deterministic_pipeline.py:1438-1443`, 1455 |
| 2 | Ordre des sections | instructions → bloc → messages | idem |
| 3 | Sections stables | le texte système de l'appelant et l'en-tête du bloc | §4.1 (219 c stables sur 100 tours) |
| 4 | Sections réécrites chaque tour | **tout le bloc** (contenu réordonné par la question), et les messages re-sélectionnés | §4.2-4.4 |
| 5 | Injection des messages récents | `verbatim_mids` = `KEEP_RECENT_BY_LEVEL` derniers + `reason == "mandatory"` | 1449-1451 |
| 6 | Récupération mémoire | `build_memory(...)` produit un `MemoryView`, rendu par `build_context_block` | 1343, 757 |
| 7 | Reconstruction complète | trois candidats construits et comparés, `reconstructed` en tête | 1476, 1515 |
| 8 | Anciens messages modifiés ou complétés ? | **modifiés** : le bloc remplace les tours anciens, la fenêtre verbatim glisse | 1450, 1455 |
| 9 | La mémoire modifie-t-elle le préfixe ? | **oui — et pas seulement la mémoire : la question seule suffit** | §4.4 |
| 10 | Boster connaît-il les cache hits ? | **non** | §8, `adapters/kv_cache.py:149-152` |

Commentaire du code à citer (l. 1433-1435) : « The system prompt is the STABLE prefix (KV-cache friendliness, Phase 2.4 §10): instructions first, then the volatile context block. The block is never injected as a fake 'user' message. »

Le principe invoqué (« mettre le volatil après le stable ») est correct **à l'échelle d'une section**, mais il est appliqué à l'intérieur de la seule zone qui serait cacheable : le system prompt. Placer le volatile après les instructions protège les instructions, pas le prompt.

## 3. Sections stables et dynamiques

**Stables** : texte système de l'appelant, bloc `[DEVELOPER]`, en-tête `[BOSTER CONTEXT]` et intitulés de sections.

**Dynamiques** (mesuré) :
- le **classement des faits** est conditionné par la question — `build_context_block`, l. 840 : « 5. relations, then 6. facts: **ranked by retrieval**, truncated by the allowance » ;
- le **jeu de sections** lui-même (une section peut céder la place à une autre selon l'allowance) — observé au tour 100 (§4.3) ;
- une section `unknown:` est **préfixée** quand la question nomme un sujet inconnu — `detect_unknown_subjects` (l. 728), appelé l. 811 ;
- les **identifiants de source** sont salés par le `conversation_id` : `message_id_for` = `"msg_" + md5(conversation_id ␟ role ␟ content ␟ timestamp ␟ occurrence)[:12]` (`boster/memory/consolidation.py:494-516`) **[OBS]** : même conversation, même contenu, `conversation_id` différent ⇒ bloc différent (ids `msg_95849ccbf650…` vs `msg_369fd95230de…`). Conséquence : deux conversations de contenu identique ne partagent **jamais** de préfixe.

## 4. Analyse des modifications du préfixe

Méthode : le prompt est sérialisé de façon canonique (`<system>…</system>` puis par message), le **plus long préfixe commun (LCP)** entre le tour *t-1* et le tour *t* est mesuré **en caractères (exact)** ; les tokens sont **estimés** (`chars/4`). Un LCP est une condition **nécessaire** de réutilisation, jamais une preuve : le flux de tokens réel dépend du template de chat du modèle.

### 4.1 Préfixe stable sur toute la conversation
LCP partagé par les 100 prompts : **219 c** (mémoire active) contre **230 c** (sans mémoire). Autrement dit, la partie réellement réutilisable de bout en bout se réduit aux instructions.

Décomposition mesurée au tour 100 (n=100, scénario B) :

| Partie du prompt | Caractères | Part |
|---|---|---|
| instructions (system appelant + `[DEVELOPER]`) | **209** | **4,0 %** |
| bloc de contexte | **4 411** | 84,2 % |
| messages retenus (8) | **616** | 11,8 % |
| **total** | **5 236** | 100 % |

Le prompt est donc **à 96 % dynamique**, et le préfixe stable utile (209 c) ne couvre pas les 1 024 premiers tokens exigés par les API compatibles OpenAI pour ouvrir un cache.

### 4.2 Régime engageant, tours 96-99 (n=100, budget 2 000)
```
t96  bloc=4 388 c  LCP=1 150 c (26,2 %)   commun : …'side. [msg_7077a7fb95cf]\n- Status check 9'
t97  bloc=4 419 c  LCP=1 150 c (26,2 %)
t98  bloc=4 386 c  LCP=1 150 c (26,0 %)
t99  bloc=4 386 c  LCP=1 150 c (26,2 %)
```
La coupure tombe **à l'intérieur de la liste des faits**, sur `'- Status check 9X'` : le classement par pertinence se réordonne dès que la question change d'un tour à l'autre. Aucune coupure ne tombe dans les instructions.

### 4.3 Rupture de section (t99 → t100) : effondrement à 2,6 %
```
t100 bloc=4 411 c  LCP=113 c (2,6 %)
   commun : …'55]\n- Redis (active) [msg_e787ca1a0629]\n'
   t-1    : …'history:\n- Memcached (superseded) -> \nre…'
   t      : …'relations:\n- Orion uses SQLite\n- The rep…'
```
La section `history` disparaît/recule et `relations` remonte : le **jeu de sections est fonction de l'allowance**, donc une variation de taille de quelques dizaines de caractères suffit à changer la structure du bloc et à effondrer le préfixe réutilisable.

### 4.4 Instabilité propre à la question (état de mémoire **figé**)
Conversation de 100 tours gelée, seule la question change :

| Question | Bloc | LCP avec la question « current » |
|---|---|---|
| `current` | 4 411 c | 100,0 % |
| `history` | 4 374 c | **2,6 %** (113 c) |
| `reason` | 4 418 c | **2,6 %** (113 c) |
| `relation` | 4 413 c | **4,5 %** (199 c) |
| `unknown` | 4 415 c | **0,4 %** (17 c) |
| `multi` | 4 426 c | **4,5 %** (199 c) |

Section d'ouverture observée : `'current decisions:'` pour cinq questions, `'unknown:'` pour la question nommant un sujet inconnu (l. 811). **La mémoire n'a pas changé ; le préfixe a changé quand même.**

### 4.5 Où tombe la coupure
Sur les 99 transitions du scénario B à n=100 : **38 coupures dans le bloc, 61 dans les messages, 0 dans les instructions**. Les 61 coupures « messages » correspondent aux tours où le pipeline n'agit pas (simple ajout en fin de conversation) : c'est le régime sain.

## 5. Résultats des scénarios 10 / 25 / 50 / 100

Table (n=100 seulement pour le régime engageant ; `LCP moy%` = moyenne de la part du prompt précédent conservée sur toutes les transitions) :

| Scénario | 10 | 25 | 50 | 100 (bloc / LCP moy% / coupures bloc) |
|---|---|---|---|---|
| **A** sans mémoire | 100,0 % | 98,8 % | 98,8 % | 0 bloc · 73,7 % · 0 |
| **B** mémoire persistante | 100,0 % | 98,8 % | 98,8 % | 38 blocs · 70,6 % · **38** |
| **C** consolidation tardive | 100,0 % | 98,8 % | 98,8 % | 38 blocs · 70,6 % · **38** |
| **D** changement de décision | 100,0 % | 98,9 % | 98,8 % | 38 blocs · 70,3 % · **38** |
| **E** correction d'un fait ancien | 100,0 % | 98,9 % | 98,8 % | 38 blocs · 70,3 % · **38** |
| **F** question historique | 100,0 % | 98,8 % | 98,8 % | 38 blocs · 70,6 % · **38** |
| **G** reconstruction complète | 100,0 % | 98,8 % | 98,8 % | 38 blocs · 70,6 % · **38** |
| **H** budget contraignant (800) | 100,0 % | 98,8 % | **63,1 %** · 49 coupures messages · 0 bloc | 50,0 % · 0 bloc |

**Lecture décisive — à 10, 25 et 50 échanges le bloc n'est jamais produit** (`bloc = 0`) : sous le budget, Boster laisse passer la conversation. Le préfixe est alors en ajout seul, ~99 % stable, et les coupures tombent en fin de conversation. **Le coût de cache de Boster n'apparaît qu'au moment où il agit, c'est-à-dire à n=100.**

- Le scénario **H** (budget serré) rogne la conversation (63,1 % à n=50, 50,0 % à n=100) **sans bloc** : la coupe est en tête de fenêtre, donc le préfixe tombe à ~37 % puis ~10 % des derniers tours. Budget serré ⇒ instabilité par glissement de fenêtre, mécanisme distinct de celui du bloc.
- **C** (consolidation tardive) est identique à **B** : la mémoire démarre au tour 12, ce qui ne change pas la *nature* de l'instabilité — elle est continue, pas ponctuelle.
- **D** et **E** (événements tardifs) ne se distinguent pas de **B** dans les agrégats, mais leur signature est nette **au tour de l'événement** — les moyennes la masquent :

| Tour | B (référence) | D (changement de décision) | E (correction d'un fait) |
|---|---|---|---|
| 98 | 26,02 % | 26,02 % | 26,02 % |
| 99 | 26,22 % | **0,87 %** (38 c) | **0,39 %** (17 c) |
| 100 | 2,58 % (113 c) | 2,54 % (112 c) | 0,39 % (17 c) |

Un changement de décision réécrit la section d'ouverture `current decisions:` ⇒ le bloc réutilisable tombe de 26 % à **0,87 %** (38 caractères). Une correction de fait encore plus bas : **0,39 %** (17 caractères, soit l'en-tête `[BOSTER CONTEXT]` seul). Autrement dit : **au moment précis où la mémoire fait son travail, elle détruit le préfixe.**

## 6. Compatibilité par moteur

| Moteur | Réutilise un préfixe identique ? | Conditions | Cache exposé ? | Statut |
|---|---|---|---|---|
| **llama.cpp** | oui, par défaut | `cache_prompt` **défaut `true`** : « only the “unseen” suffix is evaluated » ; slot réutilisé selon `-sps` ; `--cache-reuse N` (KV shifting, défaut 0 = désactivé) | **oui** : `timings.prompt_n` (tokens traités) et `timings.cache_n` (tokens réutilisés) | **[DOC]** README serveur llama.cpp + discussions #13606, #29001 |
| **LM Studio** | oui (llama.cpp sous-jacent ; MLX sur Apple : cache disque par blocs de 256 tokens, `_find_common_prefix`) | préfixe **exact** depuis le premier token jusqu'au premier token qui diffère | **non sur cette build** : `/stats`, `/props`, `/health` → `Unexpected endpoint or method`, `usage` sans `prompt_tokens_details`, `stats` vide | **[DOC]** + **[OBS]** §7.3 |
| **API compatibles OpenAI** | oui, automatique | prompt ≥ 1 024 tokens, paliers de 128 ; **un seul caractère de différence dans les 1 024 premiers tokens ⇒ cache miss** (`cached_tokens = 0`) | **oui** : `usage.prompt_tokens_details.cached_tokens` | **[DOC]** OpenAI / Azure |
| **Hermes** | oui, par conception | system prompt en 3 étages **stable → context → volatile**, construit **une fois par session** et reconstruit seulement à la compaction ; le contenu sensible au tour voyage sur un **canal séparé** pour que « the cached prefix survives » ; breakpoints Anthropic `system_and_3` (BP1 = system prompt) | selon le fournisseur | **[DOC]** docs Hermes, `prompt-assembly` + `context-compression-and-caching` |

Le point dur est la ligne OpenAI/Azure : **une différence dans les premiers 1 024 tokens annule le cache**. Le bloc de Boster commence après les instructions ; dès qu'il bouge, tout ce qui suit est recalculé — préfixe moteur, mais aussi breakpoint système côté Anthropic.

## 7. Résultats des benchmarks

Fichiers créés (seuls fichiers écrits par l'audit) :
```
benchmark/kv_cache/__init__.py
benchmark/kv_cache/conversation.py            conversation déterministe tour par tour
benchmark/kv_cache/prefix_stability.py        mesure du préfixe (3 agencements + append-only)
benchmark/kv_cache/engine_probe.py            sonde moteur réelle (GET + requêtes max_tokens=1)
benchmark/kv_cache/phase1_prefix_steps.json   dump de preuves : 32 résumés + 1 200 pas (tailles 50 et 100)
```
Aucun moteur touché. Aucune dépendance ajoutée. Reproduction : `PYTHONPATH="." uv run python -m benchmark.kv_cache.prefix_stability --json <chemin>` et `PYTHONPATH="." uv run python -m benchmark.kv_cache.engine_probe`.

### 7.1 Agencements comparés (mesuré, n=100, scénario B, tour 100)

Même contenu, ordonnancements différents — la comparaison isole l'ordre :

| Agencement | Longueur du prompt | Préfixe réutilisable (LCP) | À recalculer | Part réutilisable |
|---|---|---|---|---|
| `actual` (le pipeline) | 5 236 c | **333 c** | 4 903 c | **6,4 %** |
| `suffix` (bloc renvoyé en fin de prompt) | 5 236 c | **610 c** | 4 626 c | 11,7 % |
| `append_only` (contrôle, conversation brute en ajout seul + bloc en fin) | ≈ 15 500 c *(déduit)* | **11 126 c** | ≈ 4 400 c | 71,7 % |
| `incremental` (bloc réutilisé si la mémoire n'a pas changé) | — | — | — | **inopérant** : la signature mémoire change à chaque tour (§9.4), donc la branche n'est jamais prise |

Trois résultats, dont deux contre-intuitifs.

**1. L'agencement `suffix` fait mieux que le pipeline** (610 c contre 333 c) : dans le pipeline, la fenêtre de messages retenue — pourtant stable en tête à 400 c près — est *enterrée derrière le bloc*. La déplacer en fin de prompt ne résout rien tant que le bloc reste réécrit, mais elle ne coûte rien non plus.

**2. Le travail de préfill par tour est presque identique dans les trois agencements** (4 389 c / 4 626 c / 4 903 c, soit ±12 %) — parce que **le bloc, qui pèse 4 411 c, est réécrit dans tous les cas**. Déplacer le bloc ne réduit donc pas le préfill : seule la *stabilité* du bloc le ferait.

**3. L'économie de tokens de Boster porte exactement sur les tokens qui auraient été réutilisables.** Transmis : 5 236 c (pipeline) contre ~15 500 c (append-only) ⇒ **−66 %**, un gain réel. Mais ce qui est retiré, ce sont les messages anciens qui formaient justement le préfixe commun réutilisable. D'où le tableau :

| | Tokens transmis | Préfill à recalculer par tour | Gain de tokens | Gain de cache |
|---|---|---|---|---|
| `append_only` | ~15 500 c | ~4 400 c | — | référence |
| `actual` | 5 236 c | 4 903 c | **−66 %** | **0** (préfill inchangé) |

Autrement dit : **Boster divise par trois les tokens envoyés sans réduire d'un caractère le préfill.** Sur une API qui facture la lecture cachée à prix réduit, l'agencement `append_only` coûte d'ailleurs *plus* cher en entrée facturée (11 126 c × 0,5 + 4 389 c ≈ 9 952 unités équivalent-plein contre ≈ 5 070 pour le pipeline, à une remise de 50 %) : **la bonne cible n'est ni l'un ni l'autre, c'est un bloc stable** — §10.

### 7.2 Latence moteur (indicative)
| Requête | `prompt_tokens` (moteur) | Latence |
|---|---|---|
| tour 99, 1re | 2 360 | 4,57 s (inclut le chargement du modèle) |
| tour 99, rejouée à l'identique | 2 360 | **0,058 s** |
| tour 100 (préfixe modifié) | 2 387 | 0,257 s |
| tour 100, rejouée à l'identique | 2 387 | 0,043 s |

Le motif (identique → ~0,05 s ; préfixe légèrement différent → 0,26 s) est **cohérent** avec une réutilisation de préfixe. Ce n'est **pas une preuve** : le moteur ne rapporte aucun compteur. À traiter comme corrélation.

### 7.3 Comptage réel vs estimation
Tour 99 : `prompt_tokens` = **2 360** (moteur) contre **1 262** estimés (`chars/4`) ⇒ **facteur ×1,87**. Tous les chiffres en tokens de ce rapport sont donc des **minorations d'environ 47 %** sur ce contenu (bloc dense en identifiants hexadécimaux et en lignes courtes). Aucun tokenizer exact n'est installé (`tiktoken`, `transformers`, `tokenizers` absents).

## 8. Cache hits réellement confirmés

**Aucun.**

- `usage` de cette build LM Studio ne contient pas `prompt_tokens_details` ni `cached_tokens` **[OBS]**.
- `/stats`, `/props`, `/health` ne sont pas servis **[OBS]** — or `adapters/kv_cache.py:113` interroge précisément `GET {base_url}/stats`.
- `adapters/kv_cache.py:149-152` renvoie **délibérément des stats vides** pour les serveurs compatibles OpenAI, avec ce commentaire : « Return empty stats — the client should use **text analysis** instead ». Autrement dit, l'adaptateur prévoit explicitement de **substituer une analyse textuelle** à une information de cache.
- Conclusion : `cache_hit_rate` et `cached_tokens` restent à 0 pour LM Studio ; toute valeur non nulle qu'afficherait Boster proviendrait d'une heuristique textuelle, pas d'un cache hit.

## 9. Risques et limitations

**9.1 Défauts constatés dans `boster/adapters/kv_cache.py` (documentés, non réparés)**
- **Détection du moteur par sous-chaîne de port** (l. 68-84) : `if "11434" in self.base_url` / `"1234"` / `"8080"`. Une URL quelconque contenant `8080` est classée « llama.cpp ». Aucune sonde réelle, aucun en-tête lu.
- **Clés `/stats` vraisemblablement fausses** (l. 118-120) : `cache_filled_tokens`, `prompt_processing_tokens`, `prompt_token_cost`. Le format documenté llama.cpp expose `timings.cache_n` / `timings.prompt_n` dans la réponse de complétion, pas ces noms dans `/stats`.
- **Confusion coût / taux** (l. 121 et 142) : `cache_hit_rate=data.get("prompt_token_cost", 0.0)` — un champ de **coût** est lu comme un **taux de succès**.
- **`boster/adapters/kv_cache/` est un répertoire vide** à côté du fichier `kv_cache.py` (résidu).

**9.2 Hygiène de mesure**
- LCP **en caractères** ≠ préfixe **en tokens** : le template de chat peut insérer des tokens spéciaux et décaler la frontière. La mesure est nécessaire, pas suffisante.
- Aucun compteur moteur ⇒ §7.2 est **corrélationnel**.
- Conversation synthétique : le taux de rotation de la mémoire dépend du texte. La fixture utilise un remplissage (« Status check N… ») volontairement neutre — il n'en est pas moins extrait (§9.4).

**9.3 Écart d'identifiants selon le contexte**
`message_id_for` salant avec `conversation_id`, un appelant qui ouvre une nouvelle conversation (ou un nouvel identifiant par requête) **ne peut pas** réutiliser le bloc, même pour un contenu identique. C'est délibéré (« distinguishes two byte-identical messages ») mais son coût de cache n'est pas documenté.

**9.4 L'extraction enregistre les accusés de réception du modèle**
Observé : les faits incluent `'Status check 69: nothing changed on the storage side.'` **et** `'Acknowledged.'`. Le répertoire de faits double (2 faits par tour), le bloc grossit et son classement tourne deux fois plus vite. Effet direct sur §4.2.

**9.5 Limite moteur, non imputable à Boster**
Un préfixe changeant dans les premiers 1 024 tokens déclenche un cache miss sur les API compatibles OpenAI (Azure/OpenAI). Aucune transformation Boster ne peut contourner cette règle : elle doit placer le volatil **tard**.

## 10. Recommandations d'architecture (non appliquées)

**La priorité est la stabilité du bloc, pas son emplacement.** §7.1 : déplacer le bloc ne change pas le préfill ; seule sa stabilité le change.

1. **Rendre le bloc incrémental (priorité 1).** Ne le reconstruire que si la mémoire a réellement changé ; sinon réutiliser les octets précédents. C'est la seule action qui fait baisser le préfill par tour, puisque le bloc pèse 4 411 c sur 5 236 c. En l'état, la signature mémoire change à chaque tour (§9.4).
2. **Sortir le classement par pertinence de la partie fixe (priorité 2).** `build_context_block` l. 840 classe les faits « ranked by retrieval » : c'est ce qui fait tomber le préfixe à 0,4–4,5 % quand seule la question change (§4.4). Ordonner la partie fixe de façon stable (id, date) et rejeter le contre-ensemble pertinent en fin de prompt.
3. **Sortir le bloc du préfixe (priorité 3).** Après stabilisation, le rendre *après* les messages — dernier message ou suffixe de la dernière entrée utilisateur — et garder `analysis.system_text` + `[DEVELOPER]` comme préfixe strictement stable. C'est la règle Hermes : volatile en dernier, contenu sensible au tour sur un canal séparé.
4. **Stabiliser le jeu de sections.** Une section qui va et vient selon l'allowance (§4.3) effondre le préfixe de 26 % à 2,6 %. Fixer l'ordre et la présence, ne faire varier que la troncature interne.
5. **Ne pas saler les identifiants par `conversation_id` dans le rendu.** Conserver des ids durables pour la persistance, rendre une forme canonique stable.
6. **Étendre la fenêtre verbatim au lieu de la faire glisser.** `KEEP_RECENT_BY_LEVEL` (l. 145) laisse 4 tours verbatim : si la fenêtre s'étend, le préfixe se conserve.
7. **Mesurer avec un vrai tokenizer** avant toute décision de budget (§7.3 : facteur 1,87).
8. **Rendre l'adaptateur honnête.** Supprimer la substitution « text analysis » comme proxy de cache, corriger la confusion coût/taux, ne pas déduire le moteur du numéro de port, rapporter « non exposé » plutôt que `0`.

## 11. Plan de correction proposé (à autoriser, non appliqué)

| # | Action | Fichier | Test requis |
|---|---|---|---|
| 1 | Émettre le bloc hors `system_sections`, en suffixe de prompt | `deterministic_pipeline.py:1436-1443` | LCP du system prompt = instructions sur 100 tours ; aucun test historique cassé |
| 2 | Mémoïser le bloc sur signature mémoire stable | `deterministic_pipeline.py:1421-1431` | bloc identique octet pour octet sur mémoire inchangée |
| 3 | Sortir le classement de pertinence du bloc fixe | `deterministic_pipeline.py:840` | ordre du bloc indépendant de `query` ; ordre du suffixe dépendant |
| 4 | Fixer ordre et présence des sections | `build_context_block` | jeu de sections constant sur 100 tours |
| 5 | Rendu canonique des ids | `memory/consolidation.py:494-516` | persistance inchangée + rendu stable |
| 6 | Corriger l'adaptateur | `adapters/kv_cache.py:68-152` | « non exposé » explicite ; aucun taux inventé |
| 7 | Réintroduire un tokenizer exact | `core/tokenizer.py` | écart < 2 % vs `prompt_tokens` moteur |

Ordre recommandé : **2 → 3 → 1 → 4** (gain de cache : stabilité d'abord, emplacement ensuite), puis **6** (honnêteté), puis **5** et **7**. Aucune de ces actions ne doit être lancée sans autorisation explicite ; la présente phase est un audit.

## 12. Tableau de synthèse demandé

| Propriété | État | Preuve |
|---|---|---|
| Préfixe stable | **Partiel** (les instructions seules, 4 % du prompt) | 219 c stables sur 100 tours ; instructions = 209 c sur 5 236 c ; 0 coupure dans les instructions |
| Mémoire incrémentale | **Non validée** | 38/38 tours engageants avec réécriture du bloc ; LCP 26 % puis 2,6 % ; 0,87 % au tour d'un changement de décision |
| Réutilisation KV cache | **Non vérifiée** | aucun compteur exposé par le moteur (§8) |
| Cache hits mesurables | **Non** | `usage` sans `prompt_tokens_details` ; `/stats`, `/props`, `/health` non servis |
| Économies de tokens | **Estimées** (+ un point de mesure moteur) | `chars/4` sous-estime ×1,87 (2 360 vs 1 262 au tour 99) |
| Gain de cache du pipeline | **Nul** | préfill 4 903 c (pipeline) contre ≈ 4 400 c (append-only) : ±12 % ; les −66 % de tokens transmis portent sur les tokens réutilisables |

## 13. Contrôle final (§8 du brief)

- `git status` : aucun fichier suivi modifié (`git diff --name-only` vide).
- `HEAD` inchangé : `bf9134e336e6605bc37955e7cb5db4d84bbe998f`.
- Aucune dépendance ajoutée ; `pyproject.toml` et `uv.lock` intacts.
- Aucun commit.
- Fichiers créés par l'audit : `benchmark/kv_cache/{__init__,conversation,prefix_stability,engine_probe}.py`, `benchmark/kv_cache/phase1_prefix_steps.json` (preuves) et le présent rapport.
- Aucune correction appliquée : tout défaut constaté est **documenté**, pas réparé.

**En attente de votre autorisation explicite avant toute modification du moteur Boster.**
