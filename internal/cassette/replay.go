package cassette

import (
	"bytes"
	"fmt"
)

type LLMRequest struct {
	Provider  string
	Model     string
	InputHash string
	Params    any
}

type LLMExchange struct {
	Call     Event
	Response Event
}

type ReplayIndex struct {
	llm     []LLMExchange
	nextLLM int
}

func NewReplayIndex(events []Event) (*ReplayIndex, error) {
	exchanges, err := buildLLMExchanges(events)
	if err != nil {
		return nil, err
	}
	return &ReplayIndex{llm: exchanges}, nil
}

func (r *ReplayIndex) LLMExchanges() []LLMExchange {
	exchanges := make([]LLMExchange, len(r.llm))
	copy(exchanges, r.llm)
	return exchanges
}

func (r *ReplayIndex) MatchLLM(request LLMRequest) (Event, error) {
	if r.nextLLM >= len(r.llm) {
		return Event{}, fmt.Errorf("replay exhausted: no recorded llm exchange remains")
	}

	exchange := r.llm[r.nextLLM]
	if err := matchLLMRequest(r.nextLLM+1, exchange.Call, request); err != nil {
		return Event{}, err
	}

	r.nextLLM++
	return exchange.Response, nil
}

type llmCallState struct {
	call      Event
	response  Event
	responded bool
}

func buildLLMExchanges(events []Event) ([]LLMExchange, error) {
	var calls []llmCallState
	active := map[string]int{}

	for _, event := range events {
		switch event.EventType {
		case "llm.call":
			if event.SpanID == "" {
				return nil, fmt.Errorf("llm.call at line %d is missing span_id", event.Line)
			}
			if callIndex, ok := active[event.SpanID]; ok {
				return nil, fmt.Errorf("llm.call span_id %q at line %d is already active from line %d", event.SpanID, event.Line, calls[callIndex].call.Line)
			}
			active[event.SpanID] = len(calls)
			calls = append(calls, llmCallState{call: event})
		case "llm.response":
			if event.SpanID == "" {
				return nil, fmt.Errorf("llm.response at line %d is missing span_id", event.Line)
			}
			callIndex, ok := active[event.SpanID]
			if !ok {
				return nil, fmt.Errorf("llm.response span_id %q at line %d has no prior llm.call", event.SpanID, event.Line)
			}

			calls[callIndex].response = event
			calls[callIndex].responded = true
			delete(active, event.SpanID)
		}
	}

	exchanges := make([]LLMExchange, 0, len(calls))
	for _, state := range calls {
		if !state.responded {
			return nil, fmt.Errorf("llm.call span_id %q at line %d is missing llm.response", state.call.SpanID, state.call.Line)
		}
		exchanges = append(exchanges, LLMExchange{
			Call:     state.call,
			Response: state.response,
		})
	}
	return exchanges, nil
}

func matchLLMRequest(index int, call Event, request LLMRequest) error {
	if call.Provider != request.Provider {
		return fmt.Errorf("llm replay mismatch at exchange %d: provider mismatch: recorded %q, got %q", index, call.Provider, request.Provider)
	}
	if call.Model != request.Model {
		return fmt.Errorf("llm replay mismatch at exchange %d: model mismatch: recorded %q, got %q", index, call.Model, request.Model)
	}
	if call.InputHash != request.InputHash {
		return fmt.Errorf("llm replay mismatch at exchange %d: input_hash mismatch: recorded %q, got %q", index, call.InputHash, request.InputHash)
	}
	if err := matchParams(index, call, request.Params); err != nil {
		return err
	}
	return nil
}

func matchParams(index int, call Event, requestParams any) error {
	recordedParams, recorded := call.Raw["params"]
	requestHasParams := requestParams != nil

	if !recorded && !requestHasParams {
		return nil
	}
	if recorded != requestHasParams {
		return fmt.Errorf("llm replay mismatch at exchange %d: params mismatch: recorded %s, got %s", index, describeParams(recorded, recordedParams), describeParams(requestHasParams, requestParams))
	}

	recordedJSON, err := canonicalJSON(recordedParams)
	if err != nil {
		return fmt.Errorf("llm replay mismatch at exchange %d: recorded params: %w", index, err)
	}
	requestJSON, err := canonicalJSON(requestParams)
	if err != nil {
		return fmt.Errorf("llm replay mismatch at exchange %d: request params: %w", index, err)
	}
	if !bytes.Equal(recordedJSON, requestJSON) {
		return fmt.Errorf("llm replay mismatch at exchange %d: params mismatch: recorded %s, got %s", index, recordedJSON, requestJSON)
	}
	return nil
}

func describeParams(ok bool, value any) string {
	if !ok {
		return "<absent>"
	}
	canonical, err := canonicalJSON(value)
	if err != nil {
		return fmt.Sprintf("%#v", value)
	}
	return string(canonical)
}
