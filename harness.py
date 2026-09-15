"""Leadpoet Arena's synchronous, single-input entrypoint."""


def run_icp(icp: dict) -> list[dict]:
    from tyche_arena.runtime import run

    return run(icp)
