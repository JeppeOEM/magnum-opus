// checkdeps reads go.mod and fails if any direct dependency's "// verified: YYYY-MM-DD"
// annotation is older than 12 months, or if a direct dependency is missing the annotation.
//
// Usage (from Makefile):
//
//	go list -m -json all | go run ./internal/tools/checkdeps/main.go
package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
	"time"
)

const maxAge = 365 * 24 * time.Hour

func main() {
	gomod, err := os.Open("go.mod")
	if err != nil {
		fmt.Fprintf(os.Stderr, "checkdeps: cannot open go.mod: %v\n", err)
		os.Exit(1)
	}
	defer gomod.Close()

	now := time.Now().UTC()
	inRequire := false
	var failures []string

	scanner := bufio.NewScanner(gomod)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if line == "require (" {
			inRequire = true
			continue
		}
		if inRequire && line == ")" {
			inRequire = false
			continue
		}
		if !inRequire {
			continue
		}
		// Skip blank lines and indirect deps.
		if line == "" || strings.Contains(line, "// indirect") {
			continue
		}

		// Extract verified date annotation.
		const tag = "// verified: "
		idx := strings.Index(line, tag)
		if idx == -1 {
			// Direct dep without annotation.
			mod := strings.Fields(line)[0]
			failures = append(failures, fmt.Sprintf("  %s — missing // verified: YYYY-MM-DD annotation", mod))
			continue
		}

		dateStr := strings.TrimSpace(line[idx+len(tag):])
		// Strip any trailing comment.
		if sp := strings.IndexAny(dateStr, " \t"); sp != -1 {
			dateStr = dateStr[:sp]
		}

		verified, err := time.Parse("2006-01-02", dateStr)
		if err != nil {
			mod := strings.Fields(line)[0]
			failures = append(failures, fmt.Sprintf("  %s — invalid date %q: %v", mod, dateStr, err))
			continue
		}

		age := now.Sub(verified)
		if age > maxAge {
			mod := strings.Fields(line)[0]
			failures = append(failures, fmt.Sprintf("  %s — verified %s (%.0f days ago, limit 365)", mod, dateStr, age.Hours()/24))
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "checkdeps: scan error: %v\n", err)
		os.Exit(1)
	}

	if len(failures) > 0 {
		fmt.Fprintln(os.Stderr, "checkdeps: stale or unannotated direct dependencies:")
		for _, f := range failures {
			fmt.Fprintln(os.Stderr, f)
		}
		fmt.Fprintln(os.Stderr, "\nRe-verify each dependency and update the // verified: date in go.mod.")
		fmt.Fprintln(os.Stderr, "See DEPS.md for the re-verification procedure.")
		os.Exit(1)
	}

	fmt.Println("checkdeps: all direct dependencies verified within 12 months ✓")
}
