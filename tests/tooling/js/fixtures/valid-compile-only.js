// Valid syntax with a runtime side effect: compile-only mode must not execute it.
throw new Error('this fixture must never run');
