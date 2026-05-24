package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"sort"

	"github.com/darshan-sc/agentreplay/internal/cassette"
)

const usage = `agentreplay records, replays, diffs, and tests LLM-agent runs.

Usage:
  agentreplay validate <cassette.replay.jsonl>
  agentreplay inspect <cassette.replay.jsonl>
  agentreplay record --out <cassette.replay.jsonl> -- <command> [args...]
  agentreplay replay <cassette.replay.jsonl> -- <command> [args...]
  agentreplay diff <before.replay.jsonl> <after.replay.jsonl>
  agentreplay generate-tests <cassette...> --framework pytest --out <file>
`

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		fmt.Fprint(os.Stderr, usage)
		return nil
	}

	switch args[0] {
	case "validate":
		return runValidate(args[1:])
	case "inspect":
		return runInspect(args[1:])
	case "record", "replay", "diff", "generate-tests":
		return fmt.Errorf("%q is planned but is not implemented in this slice", args[0])
	case "-h", "--help", "help":
		fmt.Print(usage)
		return nil
	default:
		return fmt.Errorf("unknown command %q\n\n%s", args[0], usage)
	}
}

func runValidate(args []string) error {
	fs := flag.NewFlagSet("validate", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: agentreplay validate <cassette.replay.jsonl>")
	}

	report, err := cassette.ValidateFile(fs.Arg(0))
	if err != nil {
		return err
	}
	if !report.Valid() {
		for _, issue := range report.Issues {
			fmt.Fprintf(os.Stderr, "%s:%d: %s\n", report.Path, issue.Line, issue.Message)
		}
		return fmt.Errorf("validation failed: %d issue(s)", len(report.Issues))
	}

	fmt.Printf("OK: %s (%d events)\n", report.Path, report.EventCount)
	return nil
}

func runInspect(args []string) error {
	fs := flag.NewFlagSet("inspect", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: agentreplay inspect <cassette.replay.jsonl>")
	}

	report, err := cassette.ValidateFile(fs.Arg(0))
	if err != nil {
		return err
	}

	fmt.Printf("Cassette: %s\n", report.Path)
	fmt.Printf("Events: %d\n", report.EventCount)

	eventTypes := make([]string, 0, len(report.Counts))
	for eventType := range report.Counts {
		eventTypes = append(eventTypes, eventType)
	}
	sort.Strings(eventTypes)
	for _, eventType := range eventTypes {
		fmt.Printf("  %s: %d\n", eventType, report.Counts[eventType])
	}

	if report.Valid() {
		fmt.Println("Validation: ok")
		return nil
	}

	fmt.Printf("Validation: failed (%d issue(s))\n", len(report.Issues))
	for _, issue := range report.Issues {
		fmt.Printf("  line %d: %s\n", issue.Line, issue.Message)
	}
	return nil
}
