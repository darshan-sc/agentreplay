package cassette

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
)

const MaxEventBytes = 10 * 1024 * 1024

func ReadFile(path string) ([]Event, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var events []Event
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), MaxEventBytes)

	lineNumber := 0
	for scanner.Scan() {
		lineNumber++
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			continue
		}

		event, err := decodeEvent(line, lineNumber)
		if err != nil {
			return nil, fmt.Errorf("%s:%d: invalid JSON event: %w", path, lineNumber, err)
		}
		events = append(events, event)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}

	return events, nil
}
