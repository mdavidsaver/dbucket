#!/usr/bin/env python3
"""
Fetch battery statistics from UPower and plot
"""

import logging
import asyncio, datetime

import numpy
from matplotlib import pylab as PL
from matplotlib.dates import date2num

from dbucket.conn import RemoteError
from dbucket.auth import connect_bus, get_system_infos
from dbucket.proxy import createProxy

# https://upower.freedesktop.org/docs/Device.html
UPOWER = 'org.freedesktop.UPower'
UPOWER_PATH = '/org/freedesktop/UPower'
DEVICE = 'org.freedesktop.UPower.Device'

_elem = numpy.dtype([
    ('T', 'I4'),
    ('V', 'f8'),
    ('S', 'I4'),
])

@asyncio.coroutine
def getData(since):
    conn = yield from connect_bus(get_system_infos())
    try:
        UP = yield from conn.proxy(
            destination=UPOWER,
            interface=UPOWER,
            path=UPOWER_PATH,
        )

        rate, charge = {}, {}
        devices = yield from UP.EnumerateDevices()
        for dpath in devices:
            print("Device", dpath)
            dev = yield from conn.proxy(
                destination=UPOWER,
                interface=DEVICE,
                path=dpath
            )

            try:
                rate[dpath]   = yield from dev.GetHistory('rate', int(since*60), 60)
                charge[dpath] = yield from dev.GetHistory('charge', int(since*60), 60)
            except RemoteError as e:
                print(dpath, e)
                continue

        return rate, charge
    finally:
        yield from conn.close()

def getargs():
    from argparse import ArgumentParser
    P = ArgumentParser()
    P.add_argument('-d', '--debug', action='store_true', default=False)
    P.add_argument('-S', '--since', default=3*60, metavar='N', type=float, help='Plot last N minutes of data')
    return P.parse_args()

def main(args):
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    loop = asyncio.get_event_loop()
    loop.set_debug(args.debug)
    rate, charge = loop.run_until_complete(getData(args.since))

    PL.subplot(2,1,1)
    for dpath, data in rate.items():
        data = numpy.asarray(data, _elem)
        T = date2num(list(map(datetime.datetime.fromtimestamp, data['T'])))
        PL.plot_date(T, data['V'], '-*', label=dpath.split('/')[-1])
        PL.hold(True)

    PL.xlabel('time')
    PL.ylabel('rate')
    PL.grid(True)
    PL.legend()

    PL.subplot(2,1,2)
    for dpath, data in charge.items():
        data = numpy.asarray(data, _elem)
        T = date2num(list(map(datetime.datetime.fromtimestamp, data['T'])))
        PL.plot_date(T, data['V'], '-*', label=dpath.split('/')[-1])
        PL.hold(True)

    PL.xlabel('time')
    PL.ylabel('change')
    PL.grid(True)
    PL.legend()

    PL.show()

if __name__=='__main__':
    main(getargs())
