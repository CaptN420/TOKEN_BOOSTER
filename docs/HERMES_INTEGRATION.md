# Boster × Hermes — guide d'installation

Boster compresse automatiquement la sortie des outils avant qu'elle n'entre
dans le contexte du modèle (hooks `transform_terminal_output` +
`transform_tool_result`). Déterministe, stdlib-only, fail-open : une erreur ne
casse jamais un appel d'outil.

## Prérequis
- Hermes Agent installé (`hermes --version`).
- Le paquet `boster` présent quelque part (ce repo clone/local).

## 1. Installer `boster` dans la runtime Hermes (automatique, une fois)

La runtime Hermes utilise sa propre venv. On y installe `boster` en editable
pour que `import boster` marche nativement partout — plus besoin de `BOSTER_PATH`
ni de chemin codé en dur.

```bash
uv pip install --python <CHEMIN_VENV_HERMES>/Scripts/python.exe -e .
```

Retrouver la venv : `hermes doctor` affiche « Runtime venv staged
(C:\...\installs\...\environments\<id>\venv) ». Sous Windows, le Python est
`<id>\venv\Scripts\python.exe`.

> editable (`-e .`) : les modifs de `boster/` sont prises en compte sans réinstaller.
> Vérification : `cd ~ && python.exe -c "import boster; print(boster.__file__)"`.

## 2. Copier le plugin

```bash
# depuis la racine du repo Boster
cp hermes_plugin/plugin.yaml  hermes_plugin/__init__.py \
   "$LOCALAPPDATA/hermes/profiles/<profil>/plugins/boster/"
```
Le dossier cible = `plugins/<nom>` du profil actif (`def` par défaut). `git clone`
du repo → copier `boster/`, `hermes_plugin/` ; seul `hermes_plugin/` finit dans
`plugins/`.

## 3. Activer

```bash
hermes plugins enable boster
```

## 4. Vérifier que ça tire vraiment
`enable` ne prouve pas que le hook se déclenche (voir skill `hermes-agent`,
`references/plugins.md`). `enabled` ≠ « tiré dans la session courante ».

1. `hermes plugins doctor boster` → registrations: 2 hook(s).
2. Déclencher : une commande `terminal` avec >1500 chars de sortie bruyante
   (pip, pytest, barres de progression), puis comparer le log :
   ```bash
   date +%s; tail -1 "$HERMES_HOME/logs/boster_savings.jsonl"   # ts récent = tiré
   ```
   Un `ts` à quelques secondes de `date +%s` prouve le tir en direct.
3. Une session Hermes **déjà ouverte** garde son ancien jeu de plugins jusqu'au
   `/reset` ou redémarrage de l'app.

## Variables
| Env var | Par défaut | Rôle |
|---|---|---|
| `BOSTER_PATH` | (fallback) | dir du paquet `boster` — **plus requis** si installé dans la venv |
| `BOSTER_DISABLE` | unset | `1` = no-op |
| `BOSTER_MIN_CHARS` | 1500 | seuil de taille pour traiter une sortie |
| `BOSTER_MIN_SAVE` | 10 | % de gain minimal pour remplacer |
| `BOSTER_MAX_LINES` | 0 (off) | cap head/tail des résultats d'outils (lignes) |
| `BOSTER_MAX_CHARS` | 0 (off) | cap dur en caractères |

## Ce que le plugin enlève / garde
Enlève : barres de progression (`50%|###`), lignes séparatrices (`----`),
lignes préfixées `INFO/DEBUG/OK/SUCCESS`, timestamps, doublons consécutifs,
lignes pip « already satisfied/Using cached » (remplacées par un compteur).
Garde : `ERROR`/`WARNING`, tracebacks, lignes porteuses de texte (`== 3 passed ==`).

- `transform_terminal_output` : `compress` (lossy, peut retirer des lignes bruit).
- `transform_tool_result` : `safe_compress` (**jamais** lossy) — strip ANSI,
  collapse des lignes vides, cap opt-in. Ne déduplique pas, ne détecte pas de format,
  ne touche pas les fichiers source / JSON / search.

## Désactiver / retirer
```bash
hermes plugins disable boster    # ou BOSTER_DISABLE=1
hermes plugins remove boster
```

## CLI seule (hors Hermes)
`boster compress|history|budget|count` — voir `README.md`.

## Tests
```bash
uv run --no-project --with pytest python -m pytest tests -q   # 17 passants
```