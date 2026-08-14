# LSP dependency audit

## Branch and protected refs

| Ref | Required value | Observed |
|---|---|---|
| `plugin-api` | `734b18d4e1ad9f902dc37c211f438737695a8436` | exact |
| `devtools-analyze` | `57b540d8e4adfddb62ad31dbc8f0e69ca94314e2` | exact |
| `devtools-mdev` | `81a4ade8b2f9037d126685792521dfc792b464b0` | exact |
| `plugin-runtime-api` | `6de27fb0affdc090a0719a1ef0cae9df5c98437b` | exact |
| `feature/smb` | `6b4b42b8fbeb901d26045ea714664fe064da3b9f` | exact |
| `feature/smb-server` | `07db76485e8b1e0b7e4d2fd9d791556001f5b24b` | exact |

The LSP branch was created with `git switch -c devtools-lsp plugin-api`.
No protected ref was changed, rebased, merged, or pushed.

## Required runtime products

| Product | Location/authority | Branch ownership |
|---|---|---|
| LSP launcher/server | `support/devtools/movian-lsp`, `support/devtools/lsp/` | `devtools-lsp` |
| GLW/JS analyzer | `build.debug/movian-analyze`, ref `devtools-analyze` | sibling product; **not** copied |
| GLW skin context | `glwskins/flat` | base checkout |
| API metadata | `generated/movian-metadata.json` | inherited from `plugin-api` |
| TypeScript declarations | `generated/movian-api.d.ts`, `generated/movian-api-v1.d.ts` | inherited from `plugin-api` |
| mdev doctor command | protected `devtools-mdev` CLI | operational sibling; CLI registration intentionally omitted |
| LSP doctor implementation | `support/devtools/mdevlib/lspdoctor.py` | LSP-only glue in this branch |

The tracked dependency boundary is explicit:

```text
git ls-files support/devtools/analyze/**       -> 0 files
git ls-files support/devtools/mdevlib/**      -> lspdoctor.py only
```

## Path discovery

Historical defaults remain usable:

```text
<lsp-repository>/build.debug/movian-analyze
<lsp-repository>/generated/movian-metadata.json
<lsp-repository>/glwskins/flat
```

For a clean split checkout, the launcher accepts:

```text
--repository-root PATH
--analyzer PATH
--metadata PATH
--skin PATH
```

Equivalent environment overrides are:

```text
MOVIAN_LSP_ROOT
MOVIAN_ANALYZER
MOVIAN_METADATA
MOVIAN_SKIN
```

Relative overrides resolve against the selected repository root. The doctor
uses `MOVIAN_ANALYZER` when present and skips a local `make -q movian-analyze`
freshness query when analyzer source is intentionally absent. It still performs
the freshness query on a combined checkout that owns the analyzer target.

## Metadata equivalence

`python3 support/devtools/metadata/gen.py --check` passed:

```text
METADATA OK
DTS OK
DTS V1 OK
```

Normalized generated artifact comparison against M7:

- `generated/movian-api.d.ts`: equivalent after removing the revision header;
- `generated/movian-api-v1.d.ts`: equivalent after removing the revision header;
- `generated/movian-metadata.json`: same schema and inventory; the known
  source-line relocation is `glw.scopes.ui.source.line` (`1431` current versus
  `1430` M7), and `movianRevision` differs by checkout.

No API metadata is embedded in the LSP source. `Metadata` indexes the committed
artifact at runtime.

## Operational caveat

`mdev lsp doctor` command registration belongs to the protected
`devtools-mdev` branch and was not copied into a branch that has no mdev CLI
base. The LSP-specific `lspdoctor.py` is present and was executed directly;
its six checks all passed. This is an intentional branch-boundary omission,
not an unexplained missing implementation.
