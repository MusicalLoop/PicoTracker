# display.py - DisplayController for PicoTracker
# Hardware: Waveshare Pico-OLED-1.3 (SH1107 controller, 128x64, SPI1)
# Pins: DC=GP8, CS=GP9, CLK=GP10, DIN=GP11, RST=GP12
# ASCII-clean. No UTF-8 outside comments.

import utime
from machine import SPI, Pin
import framebuf
import debug

# ---------------------------------------------------------------------------
# SH1107 command bytes
# ---------------------------------------------------------------------------
_CMD_DISPLAY_OFF      = 0xAE
_CMD_DISPLAY_ON       = 0xAF
_CMD_SET_CONTRAST     = 0x81
_CMD_NORMAL_DISPLAY   = 0xA6
_CMD_REVERSE_DISPLAY  = 0xA7
_CMD_MEM_ADDR_MODE    = 0x20   # Followed by 0x01 for page addressing
_CMD_COL_ADDR_LOW     = 0x00
_CMD_COL_ADDR_HIGH    = 0x10
_CMD_PAGE_ADDR        = 0xB0
_CMD_START_LINE       = 0xDC
_CMD_SEG_REMAP_NORMAL = 0xA0
_CMD_SEG_REMAP_INV    = 0xA1
_CMD_COM_SCAN_NORMAL  = 0xC0
_CMD_COM_SCAN_INV     = 0xC8
_CMD_MUX_RATIO        = 0xA8
_CMD_DISPLAY_OFFSET   = 0xD3
_CMD_CLOCK_DIV        = 0xD5
_CMD_PRECHARGE        = 0xD9
_CMD_VCOM_DESELECT    = 0xDB
_CMD_CHARGE_PUMP      = 0xAD   # Followed by 0x8A (enable) or 0x8B
_CMD_ENTIRE_ON        = 0xA4
_CMD_ENTIRE_ON_FORCE  = 0xA5

# Redraw throttle
_REDRAW_INTERVAL_MS   = 250


class DisplayController:
    """
    Manages the SH1107 OLED over SPI.
    All draw calls write to an in-memory framebuffer.
    Call refresh() to push the buffer to the display.
    refresh() is internally throttled to ~250 ms.
    """

    def __init__(self, config):
        d = config.get("display")

        self._width  = d.get("width",  128)
        self._height = d.get("height", 64)

        # SPI peripheral
        self._spi = SPI(
            d["spi_id"],
            baudrate=20000000,
            polarity=0,
            phase=0,
            sck=Pin(d["clk_pin"]),
            mosi=Pin(d["din_pin"]),
            miso=None
        )

        self._dc  = Pin(d["dc_pin"],  Pin.OUT)
        self._cs  = Pin(d["cs_pin"],  Pin.OUT)
        self._rst = Pin(d["rst_pin"], Pin.OUT)

        # Framebuffer (monochrome, horizontal MSB)
        self._buf = bytearray(self._width * self._height // 8)
        self._fb  = framebuf.FrameBuffer(
            self._buf, self._width, self._height, framebuf.MONO_HMSB)

        self._last_refresh_ms = 0
        self._timeout_s       = d.get("timeout_s", 30)
        self._last_activity_ms = utime.ticks_ms()
        self._screen_on        = True
        self._rotation        = d.get("rotation", 180)   # 0 or 180

        self._init_display(d.get("brightness", 128))
        self.touch()   # start timeout clock from after init, not from boot
        debug.info("display: SH1107 init OK {}x{} rotation={}".format(
            self._width, self._height, self._rotation))

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_display(self, brightness):
        """Hardware reset then send init sequence."""
        self._rst(1)
        utime.sleep_ms(1)
        self._rst(0)
        utime.sleep_ms(10)
        self._rst(1)
        utime.sleep_ms(10)

        # Rotation: only column order is reversed in refresh().
        # Seg remap and COM scan are fixed to the working values from Waveshare
        # reference — changing these on this 64x128 panel causes blank output
        # because only half the SH1107's 128x128 SRAM is used.
        seg = 0xA0
        com = 0xC0

        cmds = [
            0xAE,           # display off
            0x00,           # set lower column address
            0x10,           # set higher column address
            0xB0,           # set page address
            0xDC, 0x00,     # set display start line = 0
            0x81, brightness if brightness != 128 else 0x6F,  # contrast
            0x21,           # memory addressing mode (page)
            seg,            # segment remap (rotation-dependent)
            com,            # COM scan direction (rotation-dependent)
            0xA4,           # disable entire display on
            0xA6,           # normal display (not inverted)
            0xA8, 0x3F,     # multiplex ratio 1/64
            0xD3, 0x60,     # display offset
            0xD5, 0x41,     # oscillator division
            0xD9, 0x22,     # pre-charge period
            0xDB, 0x35,     # VCOM deselect
            0xAD, 0x8A,     # charge pump enable
            0xAF,           # display on
        ]
        for cmd in cmds:
            self._write_cmd(cmd)

    # ------------------------------------------------------------------
    # SPI helpers
    # ------------------------------------------------------------------

    def _write_cmd(self, cmd):
        self._cs(1)
        self._dc(0)
        self._cs(0)
        self._spi.write(bytes([cmd]))
        self._cs(1)

    def _write_data(self, data):
        self._cs(1)
        self._dc(1)
        self._cs(0)
        if isinstance(data, int):
            self._spi.write(bytes([data]))
        else:
            self._spi.write(data)
        self._cs(1)

    # ------------------------------------------------------------------
    # Framebuffer draw API (thin wrappers around framebuf)
    # ------------------------------------------------------------------

    def clear(self, colour=0):
        self._fb.fill(colour)

    def text(self, string, x, y, colour=1):
        self._fb.text(string, x, y, colour)

    def pixel(self, x, y, colour=1):
        self._fb.pixel(x, y, colour)

    def line(self, x1, y1, x2, y2, colour=1):
        self._fb.line(x1, y1, x2, y2, colour)

    def rect(self, x, y, w, h, colour=1):
        self._fb.rect(x, y, w, h, colour)

    def fill_rect(self, x, y, w, h, colour=1):
        self._fb.fill_rect(x, y, w, h, colour)

    def hline(self, x, y, w, colour=1):
        self._fb.hline(x, y, w, colour)

    def vline(self, x, y, h, colour=1):
        self._fb.vline(x, y, h, colour)

    # ------------------------------------------------------------------
    # Display push
    # ------------------------------------------------------------------

    def refresh(self, force=False):
        """
        Push framebuffer to display.
        Throttled to _REDRAW_INTERVAL_MS unless force=True.
        Returns True if a redraw was performed.
        """
        now = utime.ticks_ms()
        if not force:
            if utime.ticks_diff(now, self._last_refresh_ms) < _REDRAW_INTERVAL_MS:
                return False

        self._last_refresh_ms = now

        # SH1107 column-major layout (display physically rotated 90 degrees).
        # Framebuffer: 128w x 64h MONO_HMSB = 16 bytes per row, 64 rows.
        #
        # 0°:   framebuffer row N -> physical col (63-N), bytes MSB-first
        # 180°: framebuffer row N -> physical col N, bytes reversed+bit-flipped
        #
        # Hardware seg/COM commands are fixed — changing them blanks this panel
        # because only half the SH1107's 128x128 SRAM is wired to pixels.
        for page in range(64):
            if self._rotation == 180:
                col = page
            else:
                col = 63 - page
            self._write_cmd(0x00 + (col & 0x0f))
            self._write_cmd(0x10 + (col >> 4))
            if self._rotation == 180:
                row_start = page * 16
                for num in range(15, -1, -1):
                    b = self._buf[row_start + num]
                    b = (b & 0xF0) >> 4 | (b & 0x0F) << 4
                    b = (b & 0xCC) >> 2 | (b & 0x33) << 2
                    b = (b & 0xAA) >> 1 | (b & 0x55) << 1
                    self._write_data(b)
            else:
                for num in range(16):
                    self._write_data(self._buf[page * 16 + num])

        return True

    # ------------------------------------------------------------------
    # Screen timeout
    # ------------------------------------------------------------------

    def touch(self):
        """Record user activity - resets screen timeout."""
        self._last_activity_ms = utime.ticks_ms()
        if not self._screen_on:
            self.on()

    def check_timeout(self):
        """Turn off display if idle for longer than timeout_s."""
        if self._screen_on and self._timeout_s > 0:
            idle_ms = utime.ticks_diff(utime.ticks_ms(), self._last_activity_ms)
            if idle_ms > self._timeout_s * 1000:
                self.off()

    def on(self):
        self._write_cmd(_CMD_DISPLAY_ON)
        self._screen_on = True
        debug.debug("display: on")

    def off(self):
        self._write_cmd(_CMD_DISPLAY_OFF)
        self._screen_on = False
        debug.debug("display: off (timeout)")

    def show_splash(self, duration_ms=5000):
        """
        Text splash: 2x scaled title, byline, then scrolls left off screen.
        duration_ms: hold time before scrolling (0 = skip entirely).
        """
        if duration_ms <= 0:
            return

        def draw_2x(text, x, y):
            """Render text at 2x scale (16px tall, 16px per char)."""
            tmp_buf = bytearray(8)
            tmp_fb  = framebuf.FrameBuffer(tmp_buf, 8, 8, framebuf.MONO_HMSB)
            cx = x
            for ch in text:
                tmp_buf[:] = bytearray(8)
                tmp_fb.text(ch, 0, 0, 1)
                for row in range(8):
                    for bit in range(8):
                        if tmp_buf[row] & (0x80 >> bit):
                            # 180° rotation byte-reverses each row in refresh(),
                            # so mirror the bit position to compensate.
                            b = (7 - bit) if self._rotation == 180 else bit
                            px = cx + b * 2
                            py = y  + row * 2
                            if 0 <= px < 128 and 0 <= py < 64:
                                self._fb.pixel(px, py, 1)
                            if 0 <= px+1 < 128 and 0 <= py < 64:
                                self._fb.pixel(px+1, py, 1)
                            if 0 <= px < 128 and 0 <= py+1 < 64:
                                self._fb.pixel(px, py+1, 1)
                            if 0 <= px+1 < 128 and 0 <= py+1 < 64:
                                self._fb.pixel(px+1, py+1, 1)
                cx += 16

        line1  = "PICO"
        line2  = "TRACKER"
        byline = "By Andy Holmes"
        x1     = (128 - len(line1)  * 16) // 2
        x2     = (128 - len(line2)  * 16) // 2
        bx     = (128 - len(byline) *  8) // 2

        def draw_frame(offset):
            self._fb.fill(0)
            draw_2x(line1,  x1 - offset, 4)
            draw_2x(line2,  x2 - offset, 26)
            self._fb.text(byline, bx - offset, 52, 1)

        # Static display
        draw_frame(0)
        self.refresh(force=True)
        utime.sleep_ms(duration_ms)

        # Scroll left off screen 4px per step (~50fps)
        for offset in range(4, 136, 4):
            draw_frame(offset)
            self.refresh(force=True)
            utime.sleep_ms(20)

        self._fb.fill(0)

    def set_rotation(self, rotation):
        """Apply rotation change immediately without hardware reinit."""
        self._rotation = rotation
        self._last_refresh_ms = 0   # Force redraw on next refresh() call
        debug.info("display: rotation={}".format(rotation))

    def set_timeout(self, timeout_s):
        """Update screen timeout value."""
        self._timeout_s = timeout_s
        debug.info("display: timeout={}s".format(timeout_s))

    def set_brightness(self, value):
        """Set contrast 0-255. Higher = brighter. Default ~111 (0x6F)."""
        value = max(0, min(255, value))
        self._write_cmd(_CMD_SET_CONTRAST)
        self._write_cmd(value)
        debug.info("display: brightness={}".format(value))

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def is_on(self):
        return self._screen_on