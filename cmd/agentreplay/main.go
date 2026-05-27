package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strings"

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
		var exitErr exitCodeError
		if errors.As(err, &exitErr) {
			os.Exit(exitErr.ExitCode())
		}
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
	case "diff":
		return runDiff(args[1:])
	case "record":
		return runRecord(args[1:])
	case "replay":
		return runReplay(args[1:])
	case "generate-tests":
		return fmt.Errorf("%q is planned but is not implemented in this slice", args[0])
	case "-h", "--help", "help":
		fmt.Print(usage)
		return nil
	default:
		return fmt.Errorf("unknown command %q\n\n%s", args[0], usage)
	}
}

var errCassetteDifferences = errors.New("cassette differences found")

type exitCodeError interface {
	error
	ExitCode() int
}

type commandExitError struct {
	command []string
	code    int
}

func (e commandExitError) Error() string {
	return fmt.Sprintf("command exited with status %d: %s", e.ExitCode(), strings.Join(e.command, " "))
}

func (e commandExitError) ExitCode() int {
	if e.code <= 0 {
		return 1
	}
	return e.code
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

func runRecord(args []string) error {
	controlArgs, commandArgs, err := splitCommandArgs(args)
	if err != nil {
		return err
	}

	fs := flag.NewFlagSet("record", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	out := fs.String("out", "", "cassette output path")
	if err := fs.Parse(controlArgs); err != nil {
		return err
	}
	if *out == "" || fs.NArg() != 0 || len(commandArgs) == 0 {
		return errors.New("usage: agentreplay record --out <cassette.replay.jsonl> -- <command> [args...]")
	}
	if err := prepareRecordOutput(*out); err != nil {
		return err
	}

	if err := runChild(commandArgs, map[string]string{
		"AGENTREPLAY_MODE":       "record",
		"AGENTREPLAY_CASSETTE":   *out,
		"AGENTREPLAY_RECORD_OUT": *out,
	}); err != nil {
		return err
	}

	report, err := cassette.ValidateFile(*out)
	if err != nil {
		return fmt.Errorf("record command completed but cassette %q could not be read: %w", *out, err)
	}
	if !report.Valid() {
		for _, issue := range report.Issues {
			fmt.Fprintf(os.Stderr, "%s:%d: %s\n", report.Path, issue.Line, issue.Message)
		}
		return fmt.Errorf("record command wrote invalid cassette: %d issue(s)", len(report.Issues))
	}

	fmt.Printf("Recorded cassette: %s (%d events)\n", report.Path, report.EventCount)
	return nil
}

func prepareRecordOutput(path string) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("prepare record output %q: %w", path, err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("prepare record output %q: %w", path, err)
	}
	return nil
}

func runReplay(args []string) error {
	cassetteArgs, commandArgs, err := splitCommandArgs(args)
	if err != nil {
		return err
	}
	if len(cassetteArgs) != 1 || len(commandArgs) == 0 {
		return errors.New("usage: agentreplay replay <cassette.replay.jsonl> -- <command> [args...]")
	}
	cassettePath := cassetteArgs[0]

	report, err := cassette.ValidateFile(cassettePath)
	if err != nil {
		return err
	}
	if !report.Valid() {
		for _, issue := range report.Issues {
			fmt.Fprintf(os.Stderr, "%s:%d: %s\n", report.Path, issue.Line, issue.Message)
		}
		return fmt.Errorf("replay cassette is invalid: %d issue(s)", len(report.Issues))
	}

	if err := runChild(commandArgs, map[string]string{
		"AGENTREPLAY_MODE":        "replay",
		"AGENTREPLAY_CASSETTE":    cassettePath,
		"AGENTREPLAY_REPLAY_PATH": cassettePath,
	}); err != nil {
		return err
	}

	fmt.Printf("Replayed cassette: %s (%d events)\n", report.Path, report.EventCount)
	return nil
}

func runDiff(args []string) error {
	fs := flag.NewFlagSet("diff", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 2 {
		return errors.New("usage: agentreplay diff <before.replay.jsonl> <after.replay.jsonl>")
	}

	report, err := cassette.DiffFiles(fs.Arg(0), fs.Arg(1))
	if err != nil {
		return err
	}
	if report.Empty() {
		fmt.Println("No cassette differences.")
		return nil
	}

	fmt.Printf("Cassette differences: %d\n", len(report.Differences))
	for _, difference := range report.Differences {
		fmt.Printf("  %s %s: %s -> %s\n", difference.Location, difference.Field, difference.Before, difference.After)
	}
	return errCassetteDifferences
}

func splitCommandArgs(args []string) ([]string, []string, error) {
	for i, arg := range args {
		if arg == "--" {
			return args[:i], args[i+1:], nil
		}
	}
	return nil, nil, errors.New("missing -- before command")
}

func runChild(commandArgs []string, env map[string]string) error {
	cmd := exec.Command(commandArgs[0], commandArgs[1:]...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = mergedEnv(os.Environ(), env)

	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return commandExitError{command: commandArgs, code: exitErr.ExitCode()}
		}
		return fmt.Errorf("run command %s: %w", strings.Join(commandArgs, " "), err)
	}
	return nil
}

func mergedEnv(base []string, overrides map[string]string) []string {
	result := make([]string, 0, len(base)+len(overrides))
	seen := map[string]struct{}{}
	for _, entry := range base {
		key, _, ok := strings.Cut(entry, "=")
		if !ok {
			result = append(result, entry)
			continue
		}
		if value, override := overrides[key]; override {
			result = append(result, key+"="+value)
			seen[key] = struct{}{}
			continue
		}
		result = append(result, entry)
		seen[key] = struct{}{}
	}
	for key, value := range overrides {
		if _, ok := seen[key]; ok {
			continue
		}
		result = append(result, key+"="+value)
	}
	return result
}
