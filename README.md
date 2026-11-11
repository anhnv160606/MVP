# MVP

## Tool #14: Preliminary electrical load

Use Python 3.12 from the repository root. Install the dependencies in a virtual environment.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-load.txt
python -B -m unittest discover -s tests -v
```

The integration test uses local files `database/A.ifc` and
`database/Playbook_dien_nha_pho_Viet_Nam_2-5_tang.xlsx`. It skips when either
file is absent. The core tests use the versioned JSON example and do not need
those local files.

```sh
python -B -m tools.calculation \
  --ifc database/A.ifc \
  --playbook database/Playbook_dien_nha_pho_Viet_Nam_2-5_tang.xlsx \
  --input examples/preliminary-load.json \
  --scope-type zone --scope-id bedroom-receptacles
```

This returns 360 VA connected and 288 VA estimated demand. Change the last line
to one of these scopes to check other totals:

- `--scope-type zone --scope-id bedroom-lighting`: 20 W connected and 20 W demand.
- `--scope-type room --scope-id 12bXDOM85FnBBK679nyVuK`: 140 W and 360 VA are the
  known portions of the connected load. Both unit totals have incomplete data.
- `--scope-type floor --scope-id '1SjtEBLuPDv9dv5HC$vbTn'`: 150 W is the known
  portion of the connected load on Floor 2. Keep the single quotes around the ID.

Copy `examples/preliminary-load.json` to a local file and pass it via `--input`
to test different values. To check the circuit gate, remove `circuit_id` from one
allowance and rerun the receptacle zone. Connected load stays 360 VA, demand becomes
null, and the result includes `MISSING_RECEPTACLE_CIRCUIT`. Add `--connected-only`
to run without a demand ruleset.

The Python entry point is `tools.calculation.calc_preliminary_load`. It consumes a
project snapshot and a Playbook. The CLI loads them from the JSON input, IFC model
and workbook. Store the input file per plan revision when reproducing earlier
results. Optional `model_revision` and `playbook_revision` fields pin the source
files using the SHA-256 strings reported under `input_revisions`.

- Use IFC room GlobalIds, Playbook load class IDs, and unique `load_id` values.
  Zones select room and load class pairs. A placement can reference a
  `requirement_id`. For quantities greater than one, assign a unique
  `requirement_unit` from 1 to the requested quantity. Questionnaire power is
  per unit and can supply a linked placement's missing power.
- Set `kind` to `equipment`, `allowance`, `receptacle`, `control` or
  `generation`. Use `input_W` and/or `input_VA` and a `power_source` for
  electrical input or an outlet design allowance. If a known device replaces an
  allowance, give both the same `outlet_id`. A receptacle's `rating_A` does not
  contribute load. An allowance's `circuit_id` means the input producer has
  confirmed it belongs to a general receptacle circuit.
- Supply `power_factor` with `pf_source` for a W/VA conversion. Connected totals
  can show the known portion with partial coverage. A peak demand total is null
  if any candidate scenario lacks enough information for that unit.
- Each scenario lists the IDs of loads that can be active together. Without
  scenarios all placed loads are active. If all scenarios are complete for a
  unit, the tool returns the highest estimate for that unit. Estimates
  from the MVP ruleset are for preliminary review.
