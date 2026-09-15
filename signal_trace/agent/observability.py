"""Passive structured investigation tracing."""
from copy import deepcopy
from datetime import datetime, timezone
import re
import time
SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential)")
def redact(value):
    if isinstance(value, dict): return {k: "[REDACTED]" if SENSITIVE.search(str(k)) else redact(v) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value
def stamp(): return datetime.now(timezone.utc).isoformat()
class InvestigationTracer:
    def __init__(self):
        self.trace={"schema_version":"1.0","incident_id":None,"started_at":None,"ended_at":None,"duration_ms":None,"iterations":[],"llm_calls":[],"tool_calls":[],"evidence_added":[],"hypothesis_changes":[],"failures":[],"retries":[],"stopping_reason":None,"final_result":None,"metrics":{"total_investigation_latency_ms":None,"tool_call_latency_ms":[],"tool_call_count":0,"llm_call_count":0,"iteration_count":0,"retry_count":0,"failure_count":0,"token_usage":None,"estimated_model_cost":None}}
        self._mono=None
    def start(self, incident):
        self.trace["incident_id"]=incident.incident_id; self.trace["started_at"]=stamp(); self._mono=time.perf_counter()
    def llm_call(self, phase, attempt, started, status, error=None, usage=None):
        event={"phase":phase,"attempt":attempt,"started_at":started,"ended_at":stamp(),"status":status}
        if error:
            event["error"]=type(error).__name__+": "+str(error); self.trace["failures"].append({"kind":"llm","phase":phase,"attempt":attempt,"error":event["error"]}); self.trace["metrics"]["failure_count"]+=1
        if usage is not None: event["token_usage"]=redact(usage); self.trace["metrics"]["token_usage"]=redact(usage)
        self.trace["llm_calls"].append(event); self.trace["metrics"]["llm_call_count"]+=1
        if attempt>1: self.trace["retries"].append({"kind":"llm","phase":phase,"attempt":attempt}); self.trace["metrics"]["retry_count"]+=1
    def tool_call(self, call, arguments, started, result, before, after, started_mono=None):
        event={"call_id":result.call_id,"name":call.name,"inputs":redact(arguments.model_dump(mode="json")),"started_at":started,"ended_at":stamp(),"status":result.status,"attempts":result.attempts,"outputs":redact(result.records),"evidence_ids":list(result.evidence_ids),"evidence_added":sorted(set(after)-set(before))}
        if result.error:
            event["error"]=result.error; self.trace["failures"].append({"kind":"tool","name":call.name,"attempt":result.attempts,"error":result.error}); self.trace["metrics"]["failure_count"]+=1
        self.trace["tool_calls"].append(event); self.trace["metrics"]["tool_call_count"]+=1
        self.trace["metrics"]["tool_call_latency_ms"].append({"call_id":result.call_id,"name":call.name,"latency_ms":max(0,(time.perf_counter()-started_mono)*1000) if started_mono else 0})
        for ref in event["evidence_added"]: self.trace["evidence_added"].append({"evidence_id":ref,"call_id":result.call_id})
        for attempt in range(2,result.attempts+1): self.trace["retries"].append({"kind":"tool","name":call.name,"attempt":attempt}); self.trace["metrics"]["retry_count"]+=1
    def assessment(self, iteration, previous, current, evidence_count):
        old={h.hypothesis_id:h for h in previous.hypotheses}; new={h.hypothesis_id:h for h in current.hypotheses}
        changes=[]
        for key in sorted(set(old)|set(new)):
            if old.get(key)!=new.get(key): changes.append({"hypothesis_id":key,"before":redact(old[key].model_dump(mode="json")) if key in old else None,"after":redact(new[key].model_dump(mode="json")) if key in new else None})
        self.trace["hypothesis_changes"].append({"iteration":iteration,"changes":changes,"confidence_before":max((h.confidence for h in old.values()),default=0),"confidence_after":max((h.confidence for h in new.values()),default=0)})
        self.trace["iterations"].append({"iteration":iteration,"evidence_count":evidence_count,"primary_hypothesis_id":current.primary_hypothesis_id,"confidence":next((h.confidence for h in current.hypotheses if h.hypothesis_id==current.primary_hypothesis_id),0)})
    def finish(self,state,result):
        self.trace["ended_at"]=stamp(); self.trace["duration_ms"]=(time.perf_counter()-self._mono)*1000; self.trace["metrics"]["total_investigation_latency_ms"]=self.trace["duration_ms"]; self.trace["metrics"]["iteration_count"]=state.iteration_count; self.trace["stopping_reason"]=state.stop_reason; self.trace["final_result"]=redact(result.model_dump(mode="json"))
    def snapshot(self): return deepcopy(self.trace)
