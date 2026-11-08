VERSION = "0.1.0"


class Fail(Exception):
    """A command failed. cli.main prints it and returns exit code 1."""
