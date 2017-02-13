import micropython
micropython.alloc_emergency_exception_buf(100)

from machine import Pin, reset, disable_irq, enable_irq
import gc

from mpu6050 import MPU

import socket
import select
import time

default_port = 8000
default_irq_pin = 4
default_write_interval = 10

class MPUServer(object):
    def __init__(self, mpu,
                 port=default_port,
                 write_interval=default_write_interval,
                 irq_pin=default_irq_pin):
        self.mpu = mpu
        self.port = port
        self.write_interval = write_interval
        self.irq_pin = irq_pin
        self.last_isr = 0
        self.flag_reset_gyro = False
        self.init_pins()
        self.init_socket()

        self.mpu.calibrate()

    def __repr__(self):
        return '<{} @ {}>'.format(self.__class__.__name__, self.port)

    def init_pins(self):
        self.pin_irq = Pin(self.irq_pin, Pin.IN, Pin.PULL_UP)
        self.pin_irq.irq(handler=self.isr, trigger=Pin.IRQ_FALLING)

    def init_socket(self):
        sock = socket.socket()
        sock.bind(('0.0.0.0', self.port))
        sock.listen(2)

        self.sock = sock

    def isr(self, pin):
        # debounce
        if time.ticks_diff(time.ticks_ms(), self.last_isr) < 10:
            return

        print('! reset gyro request')
        self.flag_reset_gyro = True
        self.last_isr = time.ticks_ms()

    def serve(self):
        print('starting mpu server on port {}'.format(self.port))

        poll = select.poll()
        poll.register(self.sock, select.POLLIN)
        clients = {}
        lastsent = 0
        lastread = 0
        while True:
            now = time.ticks_ms()
            write_dt = time.ticks_diff(now, lastsent)
            read_dt = time.ticks_diff(now, lastread)
            ready = poll.poll(max(0, 1-read_dt))

            if self.flag_reset_gyro:
                self.mpu.filter.reset_gyro()
                self.flag_reset_gyro = False

            values = self.mpu.read_position()
            lastread = now

            if write_dt >= self.write_interval:
                lastsent = time.ticks_ms()
                for c in clients.values():
                    poll.register(c[0])

            for obj, eventmask in ready:
                if obj is self.sock:
                    if eventmask & select.POLLIN:
                        cl, addr = self.sock.accept()
                        print('new connection from {}'.format(addr))
                        clients[id(cl)] = (cl, addr)
                        poll.register(cl, select.POLLOUT|select.POLLHUP)
                    else:
                        print('connection says what?')

                elif eventmask & select.POLLHUP:
                    client = clients[id(obj)]
                    print('client {} has disconnected'.format(client[1]))
                    obj.close()
                    del clients[id(obj)]
                    poll.unregister(obj)
                    gc.collect()

                elif eventmask & select.POLLOUT:
                    client = clients[id(obj)]

                    try:
                        obj.write('[')
                        first = True
                        for bunch in values:
                            if not first:
                                obj.write(',')
                            first = False
                            obj.write('[')
                            obj.write(', '.join('{:f}'.format(x) for x in bunch))
                            obj.write(']')
                        obj.write(']\n')
                    except OSError:
                        print('lost connection from {}'.format(client[1]))
                        obj.close()
                        del clients[id(obj)]
                        gc.collect()

                    poll.unregister(obj)
                else:
                    print('client says what?')

