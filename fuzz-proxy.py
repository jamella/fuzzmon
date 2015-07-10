#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import socket

from fuzz_proxy.glue import DebuggingHooks
from fuzz_proxy.monitor import PtraceDbg
from fuzz_proxy.network import Downstream


proto_table = dict(tcp=socket.SOCK_STREAM, udp=socket.SOCK_DGRAM)

def socket_type(str_):
    try:
        proto, remaining = str_.split(":", 1)
        proto = proto_table[proto.lower()]
        host, port = remaining.rsplit(":", 1)
        if host.lower() == "uds":
            family = socket.AF_UNIX
            info = (port,)
        else:
            # v4 preferred if fqdn used
            family = socket.getaddrinfo(host, port)[0][0]
            info = (host, int(port))
    except (ValueError, KeyError, socket.gaierror):
        raise argparse.ArgumentTypeError("Invalid protocol descirption argument. Expecting proto:host:port or "
                                         "proto:uds:file")
    return family, proto, info

def prepare_parser():
    parser = argparse.ArgumentParser(description="A proxy which monitors the backend application state")
    parser.add_argument("-p", "--pid", help="Attach running process specified by its identifier", type=int,
                        default=None)
    parser.add_argument("-u", "--upstream", help="Upstream server to which to connect. Format is proto:host:port or "
                        "uds:proto:file for Unix Domain Sockets", type=socket_type, required=True)
    parser.add_argument("-d", "--downstream", help="IP and port to bind to, or UDS. Format is proto:host:port or "
                        "uds:proto:file. By default, listen to TCP connections on port 25746", type=socket_type,
                        default="tcp:0.0.0.0:25746")
    parser.add_argument("-o", "--output", help="Output folder where to store the crash metadata", default="metadata")
    parser.add_argument("-s", "--session", help="A session identifier for the fuzzing session")
    parser.add_argument("-f", "--fork", help="Trace fork and child process", action="store_true")
    parser.add_argument("-e", "--trace-exec", help="Trace execve() event", action="store_true")
    parser.add_argument("-n", "--no-stdout", help="Use /dev/null as stdout/stderr, or close stdout and stderr if "
                        "/dev/null doesn't exist", action="store_true")
    parser.add_argument("-c", "--conns", help="Number of downstream connections to accept in parallel. Default is 1",
                        type=int, default=1)
    process_control_parser = parser.add_mutually_exclusive_group()
    process_control_parser.add_argument("-q", "--quit", help="Do not restart the program after a fault is detected. "
                                        "Exit cleanly", action="store_true")
    process_control_parser.add_argument("-w", "--wait", help="How long to wait for before restarting the crashed "
                                        "process", type=float, default=0)
    parser.add_argument("program", help="The command line to run and attach to", nargs=argparse.REMAINDER)
    return parser

if __name__ == "__main__":
    parser = prepare_parser()
    args = parser.parse_args()

    if args.pid is None and args.program == []:
        parser.print_help()
        parser.exit(2, "ERROR: Missing program or pid (-p)\n")
    if args.pid is not None and args.program != []:
        parser.print_help()
        parser.exit(2, "ERROR: Both program and pid (-p) provided\n")

    if args.quit:
        args.delay = -1

    to_host = lambda x: x[0] if len(x) == 1 else x

    server_socket = socket.socket(args.downstream[0], args.downstream[1])
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.setblocking(False)
    server_socket.bind(to_host(args.downstream[2]))
    server_socket.listen(args.conns)

    client_socket = socket.socket(args.upstream[0], args.upstream[1])
    client_socket.settimeout(1.0)
    server_address = to_host(args.upstream[2])

    try:
        dbg = PtraceDbg(args)
    except IOError as ioe:
        parser.exit(3, "ERROR: %s" % str(ioe))

    hooks = DebuggingHooks(dbg, args.session, args.output, args.delay)

    server = Downstream(server_socket, client_socket, server_address, hooks)
    server.serve(timeout=3)
