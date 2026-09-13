"""The scan broker.

A plain host process — started by systemd, never by Docker Compose — that is
the only thing on the machine allowed to touch the real Docker socket. It
exists so that no committed compose file, dev or prod, ever needs to mount
`/var/run/docker.sock` into a container again: the worker holds a narrow
`BrokerSandbox` (see `client.py`) instead, which can ask this process to run
exactly one of five fixed tool images, with every dangerous flag decided here
rather than by the caller.

Being outside Compose is not incidental — a broker declared as a compose
service would still need the real socket mounted into *it*, in a file the
deployment scanner reads, which defeats the entire point.
"""
