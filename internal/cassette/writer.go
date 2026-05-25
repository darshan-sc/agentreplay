package cassette

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

type Writer struct {
	buf *bufio.Writer
}

func NewWriter(w io.Writer) *Writer {
	return &Writer{buf: bufio.NewWriter(w)}
}

func (w *Writer) WriteEvent(fields map[string]any) error {
	event, err := prepareEvent(fields)
	if err != nil {
		return err
	}

	line, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal cassette event: %w", err)
	}
	if _, err := w.buf.Write(line); err != nil {
		return err
	}
	if err := w.buf.WriteByte('\n'); err != nil {
		return err
	}
	return nil
}

func (w *Writer) Flush() error {
	return w.buf.Flush()
}

func prepareEvent(fields map[string]any) (map[string]any, error) {
	event := make(map[string]any, len(fields)+1)
	for key, value := range fields {
		event[key] = value
	}

	if version, ok := event["schema_version"]; ok {
		text, ok := version.(string)
		if !ok || text != SchemaVersion {
			return nil, fmt.Errorf("unsupported schema_version %q", version)
		}
	} else {
		event["schema_version"] = SchemaVersion
	}

	eventType, ok := event["event"]
	if !ok {
		return nil, fmt.Errorf("missing event")
	}
	text, ok := eventType.(string)
	if !ok || text == "" {
		return nil, fmt.Errorf("event must be a non-empty string")
	}
	if _, ok := AllowedEvents[text]; !ok {
		return nil, fmt.Errorf("unknown event type %q", text)
	}

	if err := validatePreparedEvent(event, text); err != nil {
		return nil, err
	}

	return event, nil
}

func validatePreparedEvent(fields map[string]any, eventType string) error {
	report := Report{}
	validateEventShape(&report, Event{
		SchemaVersion: SchemaVersion,
		EventType:     eventType,
		Raw:           fields,
		Line:          1,
	})
	if report.Valid() {
		return nil
	}

	messages := make([]string, 0, len(report.Issues))
	for _, issue := range report.Issues {
		messages = append(messages, issue.Message)
	}
	return fmt.Errorf("invalid %s event: %s", eventType, strings.Join(messages, "; "))
}
