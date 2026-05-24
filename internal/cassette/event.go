package cassette

import "encoding/json"

const SchemaVersion = "0.1"

var AllowedEvents = map[string]struct{}{
	"trace.start":        {},
	"llm.call":           {},
	"llm.response":       {},
	"tool.call":          {},
	"tool.response":      {},
	"retrieval.call":     {},
	"retrieval.response": {},
	"agent.step":         {},
	"error":              {},
	"trace.end":          {},
}

type Event struct {
	SchemaVersion string         `json:"schema_version"`
	EventType     string         `json:"event"`
	TraceID       string         `json:"trace_id,omitempty"`
	SpanID        string         `json:"span_id,omitempty"`
	Name          string         `json:"name,omitempty"`
	Provider      string         `json:"provider,omitempty"`
	Model         string         `json:"model,omitempty"`
	InputHash     string         `json:"input_hash,omitempty"`
	OutputHash    string         `json:"output_hash,omitempty"`
	Status        string         `json:"status,omitempty"`
	Raw           map[string]any `json:"-"`
	Line          int            `json:"-"`
}

func decodeEvent(line []byte, lineNumber int) (Event, error) {
	var raw map[string]any
	if err := json.Unmarshal(line, &raw); err != nil {
		return Event{}, err
	}

	var event Event
	if err := json.Unmarshal(line, &event); err != nil {
		return Event{}, err
	}
	event.Raw = raw
	event.Line = lineNumber
	return event, nil
}
