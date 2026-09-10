# A GUI for gitops_reconciler: Resource Trees via YAML and DOT

ArgoCD has a lovely drill-down GUI that updates in real time and shows
you what's going on as reconciliation proceeds. Let's see if we can
emulate this for these other scenarios.

## Why not just buy one

Terraform Cloud, Spacelift, env0, and Nomad were all considered and set
aside. Short version: none of them treat Docker Compose or SSH-to-a-Pi
as a first-class target the way they treat Terraform or Nomad jobs --
`gitops_reconciler` manages five backend types (Terraform, Pulumi,
CloudFormation, Compose, Pi-over-SSH), and every commercial or
open-source option either only understands the IaC-tool half of that
list, or requires migrating Compose and the Pi onto its own
orchestration model to get any visibility at all. That's a bigger
tradeoff than "get a UI" is worth. Full comparison, if useful later, is
in git history for this file.

## What ArgoCD actually has that this doesn't

ArgoCD's drill-down view works because Kubernetes already models
everything as a typed object graph with owner references -- Deployment
owns ReplicaSet owns Pod -- so the UI's whole job is "walk the graph,
render it, overlay sync status on each node." `gitops_reconciler` has no
equivalent graph. `BackEnd.apply()` is a black box to the wrapper,
deliberately, per the design reasoning in the README -- the wrapper
doesn't know or need to know what a backend actually manages internally.

Building drill-down here means asking each backend to describe its own
internal shape, on demand, in a format generic enough that one renderer
can draw all five backends the same way without knowing anything
backend-specific.

## The shape: a recursive YAML tree

Every node, at every level, has the same five fields:

```yaml
node: <unique id>
kind: <backend-defined type, e.g. "compose-project", "container", "tf-resource">
label: <human-readable name>
status: no_change | changed | failed | unknown
children:
  - <node>
  - <node>
```

`status` is built on `ApplyResult` -- the enum every backend already
returns from `apply()`/`destroy()` (`NO_CHANGE`, `CHANGED`, `FAILED`) --
but it's worth being precise rather than calling this free reuse: a tree
node can also be `unknown`, for a child resource the backend can observe
but has no independent apply-result for (a single container inside a
Compose project that the last `apply()` didn't specifically touch, say),
and `unknown` has no `ApplyResult` member. That's a genuinely different,
four-value vocabulary that happens to share three names with the
three-value one, not the same enum reused.

Two honest ways to resolve that, not one. Either `UNKNOWN` becomes a
real fourth `ApplyResult` member -- which then raises a real question,
not just a naming one: should a top-level `apply()`/`destroy()` call
ever legitimately return `UNKNOWN`, or is that meaningful only for a
tree node describing something the backend didn't directly act on? If
the answer is "only tree nodes," then the second option is correct
instead: keep `ApplyResult` exactly as it is and give the tree its own
type, `TreeStatus = ApplyResult | Literal["unknown"]`, named and typed
as the distinct-but-related thing it actually is. Either is fine.
Describing it as painless reuse of an existing concept, the way an
earlier draft of this doc did, isn't.

### Example: a Compose project

```yaml
node: demo-app-staging
kind: compose-project
label: demo-app-staging
status: changed
children:
  - node: demo-app-staging.demo-app-1
    kind: container
    label: demo-app
    status: unknown
    meta:
      image: gitops-demo-app:latest
      ports: ["9001:8080"]
    children: []
```

### Example: a Terraform stack

```yaml
node: prod-infra
kind: tf-stack
label: prod-infra
status: no_change
children:
  - node: prod-infra.aws_instance.web
    kind: tf-resource
    label: aws_instance.web
    status: no_change
    meta:
      resource_type: aws_instance
      id: i-0abc123
    children: []
  - node: prod-infra.aws_s3_bucket.assets
    kind: tf-resource
    label: aws_s3_bucket.assets
    status: no_change
    meta:
      resource_type: aws_s3_bucket
    children: []
```

Terraform gets this almost for free -- `terraform show -json` already
produces a resource graph; a `BackEnd.resource_tree()` implementation for
`TerraformBackend` is mostly a translation, not new plumbing. Pulumi's
stack export is similar. CloudFormation has
`describe-stack-resources`. Compose and the Pi have nothing like this
today -- `docker compose ps` gives a flat container list, and the Pi
backend doesn't track individual services at all, just a whole-repo
content hash. Those two backends would need real new code to produce
even a one-level-deep tree; the format doesn't solve that, it just gives
somewhere to put the answer once each backend can produce one.

`meta` is a free-form dict, backend-specific, not touched by the
renderer -- a place for `image`, `ports`, `resource_type`, whatever's
useful to show in a detail panel without it needing to be part of the
generic tree contract.

## The tree as a Pydantic model, not a dict

Everything else in this project that crosses a boundary between backend
and wrapper -- `TerraformConfig`, `ComposeConfig`, `Status` -- is a
frozen Pydantic model, specifically so `mypy --strict` (the project's
actual lint gate; see `lint.sh`) catches a malformed field before it
reaches runtime. A resource tree crossing the same boundary should be no
different. Pydantic handles a self-referential model fine:

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

from .models import ApplyResult

TreeStatus = ApplyResult | Literal["unknown"]

class ResourceNode(BaseModel, frozen=True):
    node: str
    kind: str
    label: str
    status: TreeStatus
    meta: dict[str, Any] = Field(default_factory=dict)
    children: list["ResourceNode"] = Field(default_factory=list)
```

`BackEnd.resource_tree() -> ResourceNode` gets validated the same way
every other backend config already is. Writing this feature against raw
`dict`s, the way an earlier draft of this doc did in every example and
every function signature, would make it the one part of the codebase
where the type story quietly stops.

## Converting a tree to DOT, one node at a time

The renderer doesn't need to understand `kind` or `meta` at all -- only
`node`, `label`, `status`, and `children`. But `label` and `node` come
from live infrastructure -- container names, Terraform resource
identifiers, whatever a backend author's data contains -- and DOT source
gets built by string interpolation before being rendered to SVG and
dropped into an HTML page. A name containing an unescaped `"` closes the
attribute string early and lets the rest of that value inject arbitrary
DOT; rendered to SVG and served into a browser, that's a real path to
malformed or attacker-controlled markup, not just a cosmetic rendering
bug. Every interpolated value needs DOT's own escaping (backslash and
double-quote) before it goes anywhere near an f-string:

```python
def dot_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')

STATUS_COLOR: dict[TreeStatus, str] = {
    ApplyResult.NO_CHANGE: "lightblue",
    ApplyResult.CHANGED: "lightgreen",
    ApplyResult.FAILED: "red",
    "unknown": "lightgray",
}

def node_to_dot(n: ResourceNode) -> str:
    label = dot_escape(n.label)
    node_id = dot_escape(n.node)
    lines = [
        f'"{node_id}" [label="{label}", shape=box, style=filled, '
        f'fillcolor={STATUS_COLOR[n.status]}, URL="/node/{node_id}"];'
    ]
    for child in n.children:
        child_id = dot_escape(child.node)
        lines.append(f'"{node_id}" -> "{child_id}";')
        lines.append(node_to_dot(child))
    return "\n".join(lines)
```

Wrapped in `digraph G {{ ... }}`, that's a complete, valid DOT file for
any tree any backend produces. The function is generic across all five
backends because the tree format is generic -- adding a sixth backend
later needs zero changes here, only a new `resource_tree()`
implementation on that backend.

## The clickability trick

Graphviz already solves this -- no JavaScript framework needed. Every
node in the DOT above carries a `URL` attribute. Rendered to SVG (`dot
-Tsvg`, not PNG), Graphviz turns `URL` into a real embedded `<a href>`
around that node's shape. Drop the SVG into an HTML page and the boxes
are just links.

Two ways to use that, and the tree format supports both without any
change:

**Static, fully expanded.** Render the whole tree in one `dot` call,
`URL` points at an anchor on the same page (`#demo-app-staging.demo-app-1`)
showing that node's `meta` in a detail panel. Simplest to build; fine
as long as trees stay small, which for a handful of targets across five
backends they probably do.

**Lazy drill-down.** Render only the top level. Each node's `URL` points
at a real endpoint (`/node/<id>`) that calls that backend's
`resource_tree()` for just that subtree, converts it to DOT, and
re-renders. Click-to-expand, computed only for the part being looked at.
More moving parts, but the YAML/DOT pieces above don't change at all to
support it -- it's purely a question of whether the dashboard renders
everything up front or one click at a time.

If `node` values are dotted paths (`demo-app-staging.demo-app-1`), the
lazy endpoint needs to resolve `id` against a known allowlist of real
target names and subtree paths, not string-split it and trust the
pieces -- an unvalidated id used to pick which backend's
`resource_tree()` to call, or which subtree to walk, is the same class
of problem as the DOT escaping above: untrusted-shaped input reaching
something that acts on it. Not a pressing concern for a single-user
dashboard bound to localhost; worth deciding explicitly, not by
omission, before this runs anywhere less trusted than that.

Worth starting with the static version. The interface (`URL` per node,
independent of how much of the tree is rendered at once) means growing
into lazy loading later doesn't require redesigning the tree format or
the DOT conversion, only adding the endpoint.

## What this actually gets you, honestly

The motivating comparison was ArgoCD's view, which updates in real time
because it's backed by a controller continuously watching the API
server -- the tree changes on screen because the underlying watch
pushes a change, not because anyone reloaded a page. Nothing described
here is that. `gitops_reconciler`'s own model is one-shot `tick()` calls
on a schedule, with no equivalent long-lived watch to push from. What
this design actually produces is request-driven: a page load or a click
triggers a fresh `resource_tree()` call and a fresh `dot -Tsvg` render,
showing whatever's true at that moment, not a moment ago and not
continuously. A manual reload button, or a short poll-on-an-interval
loop in the page, is an honest description of the result -- not "real
time" in ArgoCD's sense, and worth stating plainly as a scope cut rather
than letting the opening comparison imply otherwise.

## What this would actually take to build

Build order and explanation order aren't the same thing here, worth
separating explicitly. To actually implement this, easiest-and-most-
validating first:

1. `BackEnd.resource_tree() -> ResourceNode` as a new, optional method --
   optional because not every backend can produce a real tree yet, and a
   backend that can't should be allowed to return a single leaf node
   rather than being forced to implement something fake.
2. Real implementations for `TerraformBackend` and `PulumiBackend` first,
   since `terraform show -json` and Pulumi's stack export already do
   most of the work -- the fastest way to prove the `ResourceNode`
   format and the DOT conversion actually work end to end.
3. `ComposeBackend.resource_tree()` -- `docker compose ps --format json`
   gives per-container state; turning that into one level of children
   is straightforward and doesn't require the hash-based idempotency
   scheme to change at all.
4. `PiBackend` last, or not at all -- it currently tracks one hash for
   an entire repo tree, not per-service state, so a real tree here means
   deciding what "children" even means for that backend first. A single
   leaf node ("the Pi," no children) is an honest answer until that's
   worked out.
5. The `node_to_dot()` function above, a `dot -Tsvg` subprocess call (or
   a Python Graphviz binding), and a thin FastAPI route serving the
   result.

If this ever becomes explanatory material rather than a build plan --
a book chapter, say -- that order should probably flip: open on
Compose, since that's the backend already demonstrated running live in
`DEMO.md`, and let "Terraform gets this almost for free from `terraform
show -json`" land as the payoff rather than the opener. Same instinct
as Chapter 19 of the book leading with a real transcript before design
rationale, not the other way around.

None of this touches `tick()`, `ManagedTarget`, or the lock/state-file
machinery. It's additive to the `BackEnd` contract, not a change to it --
consistent with the same "the interface only asks a question every
implementer can structurally answer" principle the README already argues
for regarding `refresh()`.
