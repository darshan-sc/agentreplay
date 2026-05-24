package cassette

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
)

type Issue struct {
	Line    int
	Message string
}

type Report struct {
	Path       string
	EventCount int
	Counts     map[string]int
	Issues     []Issue
}

func (r Report) Valid() bool {
	return len(r.Issues) == 0
}

func ValidateFile(path string) (Report, error) {
	file, err := os.Open(path)
	if err != nil {
		return Report{}, err
	}
	defer file.Close()

	report := Report{
		Path:   path,
		Counts: map[string]int{},
	}

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), MaxEventBytes)

	lineNumber := 0
	nonEmptyLine := 0
	sawTraceStart := false
	sawTraceEnd := false

	for scanner.Scan() {
		lineNumber++
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			report.add(lineNumber, "blank lines are not valid cassette events")
			continue
		}

		nonEmptyLine++
		event, err := decodeEvent(line, lineNumber)
		if err != nil {
			report.add(lineNumber, fmt.Sprintf("invalid JSON event: %v", err))
			continue
		}

		report.EventCount++
		report.Counts[event.EventType]++

		validateEventShape(&report, event)

		if event.EventType == "trace.start" {
			if nonEmptyLine != 1 {
				report.add(lineNumber, "trace.start must be the first event")
			}
			if sawTraceStart {
				report.add(lineNumber, "cassette contains more than one trace.start event")
			}
			sawTraceStart = true
		}

		if !sawTraceStart && event.EventType != "trace.start" {
			report.add(lineNumber, "cassette must start with trace.start")
		}

		if sawTraceEnd {
			report.add(lineNumber, "events cannot appear after trace.end")
		}

		if event.EventType == "trace.end" {
			sawTraceEnd = true
		}
	}
	if err := scanner.Err(); err != nil {
		report.add(lineNumber, fmt.Sprintf("read cassette: %v", err))
	}

	if report.EventCount == 0 {
		report.add(1, "cassette must contain at least one event")
	}
	if report.EventCount > 0 && !sawTraceEnd {
		report.add(lineNumber, "cassette must end with trace.end")
	}

	return report, nil
}

func validateEventShape(report *Report, event Event) {
	if event.SchemaVersion == "" {
		report.add(event.Line, "missing schema_version")
	} else if event.SchemaVersion != SchemaVersion {
		report.add(event.Line, fmt.Sprintf("unsupported schema_version %q", event.SchemaVersion))
	}

	if event.EventType == "" {
		report.add(event.Line, "missing event")
		return
	}
	if _, ok := AllowedEvents[event.EventType]; !ok {
		report.add(event.Line, fmt.Sprintf("unknown event type %q", event.EventType))
		return
	}

	switch event.EventType {
	case "trace.start":
		requireString(report, event, "trace_id")
		requireString(report, event, "name")
	case "llm.call":
		requireString(report, event, "span_id")
		requireString(report, event, "provider")
		requireString(report, event, "model")
		requireString(report, event, "input_hash")
	case "llm.response":
		requireString(report, event, "span_id")
		requireAnyUsable(report, event, "output", "output_hash")
	case "tool.call":
		requireString(report, event, "span_id")
		requireString(report, event, "name")
	case "tool.response":
		requireString(report, event, "span_id")
		requireAnyUsable(report, event, "output", "error")
	case "retrieval.call":
		requireString(report, event, "span_id")
		requireAnyUsable(report, event, "query", "input_hash")
	case "retrieval.response":
		requireString(report, event, "span_id")
		requireAnyUsable(report, event, "documents", "output_hash")
	case "agent.step":
		requireString(report, event, "name")
	case "error":
		requireString(report, event, "message")
	case "trace.end":
		requireString(report, event, "trace_id")
		requireString(report, event, "status")
	}
}

func requireString(report *Report, event Event, field string) {
	value, ok := event.Raw[field]
	if !ok {
		report.add(event.Line, fmt.Sprintf("missing %s", field))
		return
	}

	text, ok := value.(string)
	if !ok || text == "" {
		report.add(event.Line, fmt.Sprintf("%s must be a non-empty string", field))
	}
}

func requireAnyUsable(report *Report, event Event, fields ...string) {
	var invalid []string
	for _, field := range fields {
		value, ok := event.Raw[field]
		if !ok {
			continue
		}
		if message := validateAlternativeField(field, value); message != "" {
			invalid = append(invalid, message)
			continue
		}
		return
	}
	if len(invalid) > 0 {
		for _, message := range invalid {
			report.add(event.Line, message)
		}
		return
	}
	report.add(event.Line, fmt.Sprintf("missing one of: %v", fields))
}

func validateAlternativeField(field string, value any) string {
	switch field {
	case "input_hash", "output_hash", "query", "error":
		if text, ok := value.(string); !ok || text == "" {
			return fmt.Sprintf("%s must be a non-empty string", field)
		}
	case "documents":
		if _, ok := value.([]any); !ok {
			return "documents must be an array"
		}
	case "output":
		if value == nil {
			return "output must not be null"
		}
		if text, ok := value.(string); ok && text == "" {
			return "output must not be empty"
		}
	default:
		if value == nil {
			return fmt.Sprintf("%s must not be null", field)
		}
		if text, ok := value.(string); ok && text == "" {
			return fmt.Sprintf("%s must not be empty", field)
		}
	}
	return ""
}

func (r *Report) add(line int, message string) {
	r.Issues = append(r.Issues, Issue{Line: line, Message: message})
}
