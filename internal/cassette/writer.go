package cassette

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

type Writer struct {
	buf     *bufio.Writer
	privacy PrivacyOptions
}

func NewWriter(w io.Writer) *Writer {
	return NewWriterWithPrivacy(w, PrivacyOptions{Mode: PrivacySafe})
}

func NewWriterWithPrivacy(w io.Writer, options PrivacyOptions) *Writer {
	if options.Mode == "" {
		options.Mode = PrivacySafe
	}
	return &Writer{
		buf:     bufio.NewWriter(w),
		privacy: options,
	}
}

func (w *Writer) WriteEvent(fields map[string]any) error {
	event, err := prepareEvent(fields, w.privacy)
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

func prepareEvent(fields map[string]any, privacy PrivacyOptions) (map[string]any, error) {
	event, err := sanitizeEvent(fields, privacy)
	if err != nil {
		return nil, err
	}

	if version, ok := event["schema_version"]; ok {
		text, ok := version.(string)
		if !ok || text != SchemaVersion {
			return nil, fmt.Errorf("unsupported schema_version %q", version)
		}
	} else {
		event["schema_version"] = SchemaVersion
	}
	if err := refreshHashFields(event, privacy.Mode); err != nil {
		return nil, err
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

func refreshHashFields(event map[string]any, mode PrivacyMode) error {
	if mode == PrivacyHideAll {
		delete(event, "output_hash")
		return nil
	}

	if input, ok := event["input"]; ok {
		hash, err := HashValue(input)
		if err != nil {
			return err
		}
		event["input_hash"] = hash
	}
	if output, ok := event["output"]; ok {
		hash, err := HashValue(output)
		if err != nil {
			return err
		}
		event["output_hash"] = hash
	}
	if documents, ok := event["documents"]; ok {
		hash, err := HashValue(documents)
		if err != nil {
			return err
		}
		event["output_hash"] = hash
	}
	return nil
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
