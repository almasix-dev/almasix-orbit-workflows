# almasix-orbit-workflows

Orbit plugin for versioned workflows. A published document is the contract: statuses, steps, assignees, and a full Orbit form schema. Cases pin that version.

```python
from almasix_orbit_workflows import WorkflowPlugin

panel.plugin(WorkflowPlugin.make())
```

Marketplace listing: https://orbit.almasix.com/plugins/orbit-workflows/

## Develop

```bash
pip install -e '.[dev]'
pytest --cov=almasix_orbit_workflows --cov-fail-under=100
```
