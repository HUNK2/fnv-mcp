"""fnv_spec: build a plugin (.esp) from the project's JSON spec, offline (no GECK, no game).

The builder itself is authoring.Authoring and the esp package; this module is the one MCP tool around it. It took
the place of the geck_spec_validate, geck_plugin_build, geck_plugin_inspect and geck_master_lookup tools when the GECK
editor tools were removed (2026-11-18). Installing the build is fnv_install (the MO2 dev profile)."""
import json
import time
import uuid
from functools import partial
from typing import Annotated, Any, Literal

from pydantic import Field

from .authoring import Authoring, AuthoringError
from .esp.build import SpecError

DESCRIPTION = ('offline: the project spec -> a plugin. validate (dry run of the spec or an inline spec: structure, '
               'references via the master index, meshes and textures; writes nothing), build (writes the plugin and '
               'a receipt into the build folder, keeps FormIDs stable), inspect (masters, record counts, cells of '
               'the build or the installed copy), lookup (records by EditorID in the spec and the indexed masters, '
               'as "@EditorID" references for the spec)')
FNV_SPEC_TOOLS = {'fnv_spec': ('control', DESCRIPTION)}

RECEIPT_KEYS = ('plugin', 'sha256', 'size', 'recordCounts', 'formIds', 'references', 'warnings', 'validation')


class SpecToolError(ValueError):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence


class SpecTools:
    def __init__(self, project):
        self.project = project

    def authoring(self):
        if not getattr(self.project, 'plugin', None):
            raise SpecToolError('NO_SPEC', 'The project file has no "plugin" and "spec": add them to build a plugin '
                                '(bridge/docs/FNV-MCP-SETUP.md, project file settings).')
        return Authoring(self.project)

    def call(self, fn):
        try:
            return fn()
        except SpecError as e:
            raise SpecToolError('SPEC_INVALID', str(e), {'where': e.where}) from e
        except AuthoringError as e:
            raise SpecToolError(e.code, str(e), e.evidence) from e

    def validate(self, spec=None):
        receipt, _ = self.call(lambda: self.authoring().build(write=False, spec_override=spec))
        out = {k: receipt[k] for k in RECEIPT_KEYS}
        if not receipt['validation']['ok']:
            raise SpecToolError('VALIDATION_FAILED', 'The spec builds but fails validation', out)
        return out

    def build(self):
        authoring = self.authoring()
        receipt, _ = self.call(lambda: authoring.build(write=True))
        if not receipt['validation']['ok']:
            raise SpecToolError('VALIDATION_FAILED', 'The build failed validation; nothing was written',
                                receipt['validation'])
        out = {k: receipt[k] for k in RECEIPT_KEYS if k != 'formIds'}
        out.update(_outcome='built', written=receipt['written'], path=str(authoring.built_path),
                   next='fnv_install plugin_path=%s (game closed), then fnv_game launch' % authoring.built_path)
        return out

    def inspect(self, which='build'):
        return self.call(lambda: self.authoring().inspect(which))

    def lookup(self, query, record_type=None, limit=25):
        if not query or len(query) < 2:
            raise ValueError('lookup needs a query of at least 2 characters')
        return self.call(lambda: self.authoring().lookup(query, record_type, limit))

    def run(self, action, spec=None, which='build', query=None, record_type=None, limit=25):
        started = time.monotonic()
        operation_id = str(uuid.uuid4())
        try:
            if action == 'validate':
                data = self.validate(spec)
            elif action == 'build':
                data = self.build()
            elif action == 'inspect':
                data = self.inspect(which)
            else:
                data = self.lookup(query, record_type, limit)
            outcome = data.pop('_outcome', 'unchanged')
            return {'ok': True, 'operationId': operation_id, 'operation': 'fnv_spec',
                    'projectId': self.project.project_id, 'outcome': outcome, 'data': data, 'error': None,
                    'elapsedMs': round((time.monotonic() - started) * 1000)}
        except Exception as e:  # reported, never raised to the client
            code = getattr(e, 'code', None)
            code = code if isinstance(code, str) and code else \
                'INVALID_ARGUMENT' if isinstance(e, ValueError) else 'INTERNAL_ERROR'
            message, data = str(e), getattr(e, 'evidence', None)
            recovery = 'Fix the spec, the input or the project file and call again.'
            if code == 'INTERNAL_ERROR':
                from .fnv_conv import internal_error, next_step
                (message, data), recovery = internal_error(e), next_step('INTERNAL_ERROR')
            # build writes the plugin and the receipt last, atomically: a failure before that changed nothing
            return {'ok': False, 'operationId': operation_id, 'operation': 'fnv_spec',
                    'projectId': self.project.project_id, 'outcome': 'failed_before_change',
                    'data': data,
                    'error': {'code': code, 'message': message, 'retrySafe': True, 'recovery': recovery},
                    'elapsedMs': round((time.monotonic() - started) * 1000)}


def register(server, project, annotations, result_type):
    """Add fnv_spec to the server (game toolset)."""
    import anyio
    from mcp.types import CallToolResult, TextContent

    tools = SpecTools(project)

    @server.tool(annotations=annotations)
    async def fnv_spec(action: Literal['validate', 'build', 'inspect', 'lookup'],
                       spec: dict[str, Any] | None = None,
                       which: Literal['build', 'installed'] = 'build',
                       query: Annotated[str, Field(min_length=2, max_length=100)] | None = None,
                       record_type: Annotated[str, Field(pattern=r'^[A-Z_]{4}$')] | None = None,
                       limit: Annotated[int, Field(ge=1, le=100)] = 25) -> result_type:
        """Build a plugin (.esp) from the project's JSON spec, offline (no GECK, no game). validate [spec]: dry
        run of the project spec, or of an inline spec object: structure, references via the master index, meshes
        and textures; writes nothing. build: writes the plugin and a receipt (FormIDs, references, validation)
        into the build folder and keeps FormIDs stable across builds; refuses when validation fails. A script is
        emitted compiled once fnv_scripts_compile has cached it from the game. inspect [which=build|installed]:
        masters, record counts and cells of the build or of the installed copy (the MO2 dev profile mod, else
        Data). lookup query [record_type] [limit]: records by EditorID in the spec and the indexed masters, with
        the "@EditorID" reference to use in the spec. Install a build with fnv_install."""
        result = await anyio.to_thread.run_sync(partial(tools.run, action, spec=spec, which=which, query=query,
                                                        record_type=record_type, limit=limit))
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result))],
                              structured_content=result, is_error=not result['ok'])

    return tools

