package cassette

import (
	"bytes"
	"fmt"
	"sort"
	"strconv"
)

const maxDiffValueLength = 240

type Difference struct {
	Location string
	Field    string
	Before   string
	After    string
}

type DiffReport struct {
	BeforePath  string
	AfterPath   string
	Differences []Difference
}

func (r DiffReport) Empty() bool {
	return len(r.Differences) == 0
}

func DiffFiles(beforePath string, afterPath string) (DiffReport, error) {
	beforeEvents, err := readValidCassette(beforePath)
	if err != nil {
		return DiffReport{}, err
	}
	afterEvents, err := readValidCassette(afterPath)
	if err != nil {
		return DiffReport{}, err
	}

	report, err := DiffEvents(beforeEvents, afterEvents)
	if err != nil {
		return DiffReport{}, err
	}
	report.BeforePath = beforePath
	report.AfterPath = afterPath
	return report, nil
}

func DiffEvents(beforeEvents []Event, afterEvents []Event) (DiffReport, error) {
	report := DiffReport{}

	compareEventSequence(&report, beforeEvents, afterEvents)
	compareNonLLMEvents(&report, beforeEvents, afterEvents)
	if err := compareLLMExchanges(&report, beforeEvents, afterEvents); err != nil {
		return DiffReport{}, err
	}

	return report, nil
}

func readValidCassette(path string) ([]Event, error) {
	validation, err := ValidateFile(path)
	if err != nil {
		return nil, err
	}
	if !validation.Valid() {
		return nil, invalidCassetteError(validation)
	}

	events, err := ReadFile(path)
	if err != nil {
		return nil, err
	}
	return events, nil
}

func invalidCassetteError(report Report) error {
	if len(report.Issues) == 0 {
		return fmt.Errorf("%s is not a valid cassette", report.Path)
	}

	first := report.Issues[0]
	message := fmt.Sprintf("%s is not a valid cassette: line %d: %s", report.Path, first.Line, first.Message)
	if len(report.Issues) > 1 {
		message += fmt.Sprintf(" (+%d more issue(s))", len(report.Issues)-1)
	}
	return fmt.Errorf("%s", message)
}

func compareEventSequence(report *DiffReport, beforeEvents []Event, afterEvents []Event) {
	common := min(len(beforeEvents), len(afterEvents))
	for i := 0; i < common; i++ {
		beforeEvent := beforeEvents[i]
		afterEvent := afterEvents[i]
		if beforeEvent.EventType != afterEvent.EventType {
			report.add("event "+strconv.Itoa(i+1), "event", beforeEvent.EventType, afterEvent.EventType)
		}
	}

	for i := common; i < len(beforeEvents); i++ {
		report.addFormatted("event "+strconv.Itoa(i+1), "event", describeEvent(beforeEvents[i]), "<missing>")
	}
	for i := common; i < len(afterEvents); i++ {
		report.addFormatted("event "+strconv.Itoa(i+1), "event", "<missing>", describeEvent(afterEvents[i]))
	}
}

func compareNonLLMEvents(report *DiffReport, beforeEvents []Event, afterEvents []Event) {
	common := min(len(beforeEvents), len(afterEvents))
	for i := 0; i < common; i++ {
		beforeEvent := beforeEvents[i]
		afterEvent := afterEvents[i]
		if beforeEvent.EventType != afterEvent.EventType || isLLMEvent(beforeEvent.EventType) {
			continue
		}

		location := fmt.Sprintf("event %d %s", i+1, beforeEvent.EventType)
		compareFieldMaps(report, location, beforeEvent.Raw, afterEvent.Raw)
	}
}

func compareLLMExchanges(report *DiffReport, beforeEvents []Event, afterEvents []Event) error {
	beforeIndex, err := NewReplayIndex(beforeEvents)
	if err != nil {
		return fmt.Errorf("before cassette llm exchanges: %w", err)
	}
	afterIndex, err := NewReplayIndex(afterEvents)
	if err != nil {
		return fmt.Errorf("after cassette llm exchanges: %w", err)
	}

	beforeExchanges := beforeIndex.LLMExchanges()
	afterExchanges := afterIndex.LLMExchanges()

	common := min(len(beforeExchanges), len(afterExchanges))
	for i := 0; i < common; i++ {
		beforeExchange := beforeExchanges[i]
		afterExchange := afterExchanges[i]

		callLocation := fmt.Sprintf("llm exchange %d call", i+1)
		for _, field := range []string{"provider", "model", "input_hash", "params"} {
			compareField(report, callLocation, field, beforeExchange.Call.Raw, afterExchange.Call.Raw)
		}

		responseLocation := fmt.Sprintf("llm exchange %d response", i+1)
		compareField(report, responseLocation, "output_hash", beforeExchange.Response.Raw, afterExchange.Response.Raw)
		compareResponseOutput(report, responseLocation, beforeExchange.Response, afterExchange.Response)
	}

	for i := common; i < len(beforeExchanges); i++ {
		report.addFormatted("llm exchange "+strconv.Itoa(i+1), "exchange", describeLLMExchange(beforeExchanges[i]), "<missing>")
	}
	for i := common; i < len(afterExchanges); i++ {
		report.addFormatted("llm exchange "+strconv.Itoa(i+1), "exchange", "<missing>", describeLLMExchange(afterExchanges[i]))
	}

	return nil
}

func compareFieldMaps(report *DiffReport, location string, beforeFields map[string]any, afterFields map[string]any) {
	keys := make([]string, 0, len(beforeFields)+len(afterFields))
	seen := map[string]struct{}{}
	for key := range beforeFields {
		if shouldIgnoreDiffField(key) {
			continue
		}
		keys = append(keys, key)
		seen[key] = struct{}{}
	}
	for key := range afterFields {
		if shouldIgnoreDiffField(key) {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		compareField(report, location, key, beforeFields, afterFields)
	}
}

func compareField(report *DiffReport, location string, field string, beforeFields map[string]any, afterFields map[string]any) {
	beforeValue, beforeOK := beforeFields[field]
	afterValue, afterOK := afterFields[field]

	switch {
	case !beforeOK && !afterOK:
		return
	case !beforeOK:
		report.addFormatted(location, field, "<missing>", formatDiffValue(afterValue))
	case !afterOK:
		report.addFormatted(location, field, formatDiffValue(beforeValue), "<missing>")
	case !jsonValuesEqual(beforeValue, afterValue):
		report.add(location, field, beforeValue, afterValue)
	}
}

func compareResponseOutput(report *DiffReport, location string, beforeResponse Event, afterResponse Event) {
	beforeOutput, beforeOK := responseOutputFingerprint(beforeResponse)
	afterOutput, afterOK := responseOutputFingerprint(afterResponse)

	switch {
	case !beforeOK && !afterOK:
		return
	case !beforeOK:
		report.addFormatted(location, "output", "<missing>", afterOutput)
	case !afterOK:
		report.addFormatted(location, "output", beforeOutput, "<missing>")
	case beforeOutput != afterOutput:
		report.addFormatted(location, "output", beforeOutput, afterOutput)
	}
}

func responseOutputFingerprint(response Event) (string, bool) {
	output, ok := response.Raw["output"]
	if !ok {
		return "", false
	}

	hash, err := HashValue(output)
	if err != nil {
		return fmt.Sprintf("<unhashable output: %v>", err), true
	}
	return hash, true
}

func jsonValuesEqual(before any, after any) bool {
	beforeJSON, beforeErr := canonicalJSON(before)
	afterJSON, afterErr := canonicalJSON(after)
	if beforeErr != nil || afterErr != nil {
		return fmt.Sprintf("%#v", before) == fmt.Sprintf("%#v", after)
	}
	return bytes.Equal(beforeJSON, afterJSON)
}

func shouldIgnoreDiffField(field string) bool {
	switch field {
	case "event", "trace_id", "span_id", "parent_span_id", "timestamp", "started_at", "ended_at", "latency_ms", "duration_ms":
		return true
	default:
		return false
	}
}

func isLLMEvent(eventType string) bool {
	return eventType == "llm.call" || eventType == "llm.response"
}

func describeEvent(event Event) string {
	if event.EventType == "" {
		return "<unknown event>"
	}
	return event.EventType
}

func describeLLMExchange(exchange LLMExchange) string {
	return fmt.Sprintf(
		"provider=%s model=%s input_hash=%s response=%s",
		fieldString(exchange.Call, "provider"),
		fieldString(exchange.Call, "model"),
		fieldString(exchange.Call, "input_hash"),
		responseSummary(exchange.Response),
	)
}

func fieldString(event Event, field string) string {
	value, ok := event.Raw[field].(string)
	if !ok || value == "" {
		return "<missing>"
	}
	return value
}

func responseSummary(response Event) string {
	if outputHash, ok := response.Raw["output_hash"].(string); ok && outputHash != "" {
		return outputHash
	}
	outputHash, ok := responseOutputFingerprint(response)
	if !ok {
		return "<missing>"
	}
	return outputHash
}

func (r *DiffReport) add(location string, field string, before any, after any) {
	r.addFormatted(location, field, formatDiffValue(before), formatDiffValue(after))
}

func (r *DiffReport) addFormatted(location string, field string, before string, after string) {
	r.Differences = append(r.Differences, Difference{
		Location: location,
		Field:    field,
		Before:   before,
		After:    after,
	})
}

func formatDiffValue(value any) string {
	if text, ok := value.(string); ok {
		return truncateDiffValue(strconv.Quote(text))
	}

	canonical, err := canonicalJSON(value)
	if err != nil {
		return truncateDiffValue(fmt.Sprintf("%#v", value))
	}
	return truncateDiffValue(string(canonical))
}

func truncateDiffValue(value string) string {
	if len(value) <= maxDiffValueLength {
		return value
	}
	return value[:maxDiffValueLength] + "..."
}
