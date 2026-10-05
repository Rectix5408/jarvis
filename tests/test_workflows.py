# SPDX-License-Identifier: Apache-2.0
import pytest
from backend.operations import OperationsError, OperationsStore
from backend.workflows import WorkflowRuntime, WorkflowStore


def graph(**changes):
    value={"name":"Morning Brief","description":"","enabled":True,
           "nodes":[{"id":"start","type":"TRIGGER","config":{}},{"id":"format","type":"TRANSFORM","config":{"template":"Report: {value}"}},{"id":"done","type":"RESULT","config":{}}],
           "edges":[{"source":"start","target":"format"},{"source":"format","target":"done"}]}
    value.update(changes); return value


@pytest.fixture()
def stores(tmp_path):
    operations=OperationsStore(tmp_path/"operations"); operations.start()
    workflows=WorkflowStore(tmp_path/"workflows"); workflows.start()
    return workflows,operations


def test_validation_rejects_cycles_and_disconnected_nodes(stores):
    store,_=stores
    broken=graph(edges=[{"source":"start","target":"format"},{"source":"format","target":"start"},{"source":"format","target":"done"}])
    with pytest.raises(OperationsError,match="TRIGGER|Zyklus"): store.save(broken,"alice",[])
    broken=graph(nodes=graph()["nodes"]+[{"id":"loose","type":"RESULT","config":{}}])
    with pytest.raises(OperationsError,match="verbunden"): store.save(broken,"alice",[])


def test_agent_references_are_permission_scoped(stores):
    store,_=stores
    nodes=[{"id":"start","type":"TRIGGER","config":{}},{"id":"agent","type":"AGENT","config":{"agent_id":"foreign"}},{"id":"done","type":"RESULT","config":{}}]
    with pytest.raises(OperationsError,match="Agent fehlt"): store.save(graph(nodes=nodes,edges=[{"source":"start","target":"agent"},{"source":"agent","target":"done"}]),"alice",[])


@pytest.mark.asyncio
async def test_persisted_transform_workflow_runs_to_completion(stores):
    store,operations=stores
    workflow=store.save(graph(),"alice",[])
    runtime=WorkflowRuntime(store,operations,None,lambda user:False)
    run=await runtime.start(workflow,"status ok","alice")
    await runtime.tasks[run["id"]]
    finished=store.get_run(run["id"],"alice")
    assert finished["status"]=="COMPLETED"
    assert finished["result"]=="Report: status ok"
    assert [event["kind"] for event in finished["events"]].count("NODE_COMPLETED")==3
    with pytest.raises(OperationsError): store.get_run(run["id"],"bob")


def test_restart_interrupts_active_workflow(stores):
    store,_=stores; workflow=store.save(graph(),"alice",[]); run=store.create_run(workflow,"x","alice")
    reopened=WorkflowStore(store.directory); reopened.start()
    assert reopened.get_run(run["id"],"alice")["status"]=="INTERRUPTED"
