def run(session, line):
    """Execute a CLI line, run the simulation until any job finishes, return output."""
    buf = []
    job = session.execute(line, buf.append)
    if job is not None:
        session.device.engine.run_until(lambda: job.done)
    return "".join(buf)


def run_all(session, lines):
    return "".join(run(session, l) for l in lines.strip().splitlines())
