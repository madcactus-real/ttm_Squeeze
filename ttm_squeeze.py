import matplotlib
matplotlib.use('TkAgg')
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
from matplotlib.widgets import TextBox
from matplotlib.animation import FuncAnimation
from datetime import datetime, timedelta
from ib_insync import *
import atexit
import pytz
import tkinter as tk
from tkinter import messagebox
import sys
import time
import holidays

class TTMSqueeze:
    def __init__(self):
        self.ib = IB()
        self.tk_root = None
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        self.animation = None
        self.market_closed_anim = None
        self.rapid_blink_anim = None
        self.global_df = None
        self.zoom_level = 1.0
        self.dragging = False
        self.drag_start_x = None
        self.status_text = None
        self.analysis_text = None
        self.initial_height = self.fig.get_figheight()
        self.crosshair_vline1 = None
        self.crosshair_hline1 = None
        self.crosshair_vline2 = None
        self.is_crosshair_visible = False
        self.last_mouse_xdata = None
        self.last_mouse_ydata = None
        self.ax1_ylim = None
        self.last_analysis_message = None
        self.market_closed_blink_state = True
        self.current_message_color = 'white'
        self.dialog_active = False
        self.initial_skip_counter = 1
        self.es = Future(symbol='ES', lastTradeDateOrContractMonth='20250620', exchange='CME', currency='USD')
        self.us_holidays = holidays.US(years=[2025, 2026])

    def get_tk_root(self):
        if self.tk_root is None:
            self.tk_root = tk.Tk()
            self.tk_root.withdraw()
        return self.tk_root

    def connect_ib(self):
        try:
            if not self.ib.isConnected():
                self.ib.connect('127.0.0.1', 7496, clientId=123, timeout=10)
            time.sleep(2)
            return True
        except (ConnectionRefusedError, asyncio.TimeoutError, Exception):
            return False

    def check_api_health(self):
        for attempt in range(3):
            try:
                self.ib.reqCurrentTime()
                return True
            except Exception:
                time.sleep(1)
        return False

    def show_connection_popup(self):
        tk_root = self.get_tk_root()
        response = messagebox.askretrycancel(
            title="Connection Error",
            message="Failed to connect to IBKR API. Retry or Cancel?",
            icon="error",
            parent=tk_root
        )
        return response

    def show_warning_popup(self, title, message):
        tk_root = self.get_tk_root()
        messagebox.showwarning(title, message, parent=tk_root)

    def show_disconnect_dialog(self):
        tk_root = self.get_tk_root()
        response = messagebox.askyesno(
            title="Connection Lost",
            message="Connection to IBKR API was closed externally. Would you like to attempt to reconnect? (Selecting 'No' will close the program.)",
            icon="error",
            parent=tk_root
        )
        return response

    def on_error(self, reqId, errorCode, errorString, contract):
        informational_codes = {2100, 2101, 2102, 2103, 2104, 2105, 2106, 2107, 2108, 2109, 2110, 2158}
        if errorCode not in informational_codes:
            self.show_warning_popup("IBKR API Error", f"Error {errorCode}: {errorString}")

    def setup_plot(self):
        plt.ion()
        self.fig.canvas.manager.set_window_title("MadCactus' TTM Squeeze")
        self.fig.set_facecolor('black')
        plt.subplots_adjust(left=0.15, bottom=0.1, top=0.85, hspace=0.2)
        self.ax1.set_facecolor('#D3D3D3')
        self.ax2.set_facecolor('#D3D3D3')

    def setup_text_boxes(self):
        text_box_ax = plt.axes([0.60, 0.95, 0.18, 0.03])
        text_box = TextBox(text_box_ax, 'Future Symbol: ', initial='ES', color='black', hovercolor='gray')
        text_box_ax.set_facecolor('black')
        text_box_ax.set_visible(True)
        text_box_ax.patch.set_edgecolor('white')
        text_box_ax.patch.set_linewidth(1)
        text_box.label.set_color('white')
        for child in text_box_ax.get_children():
            if isinstance(child, plt.Text):
                child.set_color('white')

        text_box_month_ax = plt.axes([0.80, 0.95, 0.18, 0.03])
        text_box_month = TextBox(text_box_month_ax, 'Contract Month: ', initial='20250620', color='black', hovercolor='gray')
        text_box_month_ax.set_facecolor('black')
        text_box_month_ax.set_visible(True)
        text_box_month_ax.patch.set_edgecolor('white')
        text_box_month_ax.patch.set_linewidth(1)
        text_box_month.label.set_color('white')
        for child in text_box_month_ax.get_children():
            if isinstance(child, plt.Text):
                child.set_color('white')

        text_box.on_submit(self.submit_symbol)
        text_box_month.on_submit(self.submit_contract_month)

    def submit_symbol(self, text):
        new_symbol = text.strip().upper()
        contract_month = self.fig.axes[1].get_children()[0].text.strip()
        if new_symbol and contract_month:
            self.es = Future(symbol=new_symbol, lastTradeDateOrContractMonth=contract_month, exchange='CME', currency='USD')
            try:
                self.ib.qualifyContracts(self.es)
                self.setup_realtime_bars()
                self.setup_realtime_bars()
                self.global_df = None
                self.fig.canvas.draw_idle()
            except Exception:
                self.show_warning_popup("Contract Error", f"Failed to qualify contract: {self.es}. Please check the contract details.")

    def submit_contract_month(self, text):
        new_symbol = self.fig.axes[0].get_children()[0].text.strip().upper()
        contract_month = text.strip()
        if new_symbol and contract_month:
            self.es = Future(symbol=new_symbol, lastTradeDateOrContractMonth=contract_month, exchange='CME', currency='USD')
            try:
                self.ib.qualifyContracts(self.es)
                self.setup_realtime_bars()
                self.setup_realtime_bars()
                self.global_df = None
                self.fig.canvas.draw_idle()
            except Exception:
                self.show_warning_popup("Contract Error", f"Failed to qualify contract: {self.es}. Please check the contract details.")

    def update_y_ticks(self):
        current_height = self.fig.get_figheight()
        height_ratio = current_height / self.initial_height
        zoom_factor = 1 / self.zoom_level
        tick_factor = height_ratio * zoom_factor

        ax1_base_ticks = 10
        ax2_base_ticks = 5
        ax1_nbins = min(12, max(3, int(ax1_base_ticks * tick_factor)))
        ax2_nbins = min(6, max(3, int(ax2_base_ticks * tick_factor)))

        self.ax1.yaxis.set_major_locator(ticker.MultipleLocator(1))
        self.ax1.yaxis.set_major_formatter(ticker.FormatStrFormatter('%d'))
        self.ax1.yaxis.set_minor_locator(ticker.NullLocator())
        self.ax1.yaxis.set_major_locator(ticker.MaxNLocator(nbins=ax1_nbins, integer=True, prune='both'))
        plt.setp(self.ax1.yaxis.get_majorticklabels(), fontsize=6, color='white')

        self.ax2.yaxis.set_major_locator(ticker.MultipleLocator(1))
        self.ax2.yaxis.set_major_formatter(ticker.FormatStrFormatter('%d'))
        self.ax2.yaxis.set_minor_locator(ticker.NullLocator())
        self.ax2.yaxis.set_major_locator(ticker.MaxNLocator(nbins=ax2_nbins, integer=True, prune='both'))
        plt.setp(self.ax2.yaxis.get_majorticklabels(), fontsize=6, color='white')

    def on_axes_enter(self, event):
        if event.inaxes in [self.ax1, self.ax2]:
            self.is_crosshair_visible = True
            self.fig.canvas.draw_idle()

    def on_axes_leave(self, event):
        if event.inaxes in [self.ax1, self.ax2]:
            self.is_crosshair_visible = False
            if self.crosshair_vline1 is not None:
                self.crosshair_vline1.set_visible(False)
                self.crosshair_hline1.set_visible(False)
                self.crosshair_vline2.set_visible(False)
            self.last_mouse_xdata = None
            self.last_mouse_ydata = None
            if self.fig.canvas.toolbar:
                self.fig.canvas.toolbar.set_message('')
            self.fig.canvas.draw_idle()

    def on_mouse_move(self, event):
        if not self.is_crosshair_visible or event.inaxes not in [self.ax1, self.ax2] or self.dragging:
            return
        if self.global_df is None:
            return

        xdata = event.xdata
        ydata = event.ydata
        if xdata is None or ydata is None:
            return

        if self.crosshair_vline1 is None:
            self.crosshair_vline1 = self.ax1.axvline(x=xdata, color='red', linewidth=0.5, visible=True)
            self.crosshair_hline1 = self.ax1.axhline(y=0, color='red', linewidth=0.5, visible=True)
            self.crosshair_vline2 = self.ax2.axvline(x=xdata, color='red', linewidth=0.5, visible=True)
        else:
            self.crosshair_vline1.set_xdata([xdata, xdata])
            self.crosshair_vline2.set_xdata([xdata, xdata])

        if event.inaxes == self.ax1:
            self.crosshair_hline1.set_ydata([ydata, ydata])
            self.last_mouse_ydata = ydata
        else:
            y_pixel = event.y
            ydata_ax1 = self.ax1.transData.inverted().transform([0, y_pixel])[1]
            self.crosshair_hline1.set_ydata([ydata_ax1, ydata_ax1])
            self.last_mouse_ydata = ydata_ax1

        self.crosshair_vline1.set_visible(True)
        self.crosshair_hline1.set_visible(True)
        self.crosshair_vline2.set_visible(True)

        if self.ax1_ylim is not None:
            self.ax1.set_ylim(self.ax1_ylim)

        if self.fig.canvas.toolbar:
            time_str = mdates.num2date(xdata).strftime('%H:%M')
            df = self.global_df.copy()
            df['num_date'] = mdates.date2num(df.index)
            idx = (df['num_date'] - xdata).abs().idxmin()
            last_price = df.loc[idx, 'close']
            if event.inaxes == self.ax1:
                y_value = f"Last: {last_price:.2f}"
            else:
                y_value = f"Momentum: {ydata:.2f}"
            self.fig.canvas.toolbar.set_message(f"{time_str}, {y_value}")

        self.last_mouse_xdata = xdata
        self.fig.canvas.draw_idle()

    def on_resize(self, event):
        self.update_y_ticks()
        self.fig.canvas.draw_idle()

    def on_press(self, event):
        if event.inaxes in [self.ax1, self.ax2] and event.button == 1:
            self.dragging = True
            self.drag_start_x = event.xdata

    def on_motion(self, event):
        if not self.dragging or event.xdata is None or self.drag_start_x is None:
            return
        if event.inaxes in [self.ax1, self.ax2]:
            dx = self.drag_start_x - event.xdata
            x_min, x_max = self.ax1.get_xlim()
            new_min = x_min + dx
            new_max = x_max + dx

            if self.global_df is not None and len(self.global_df) >= 2:
                data_min = mdates.date2num(self.global_df.index[0])
                data_max = mdates.date2num(self.global_df.index[-1])
                buffer = 0.05 * (data_max - data_min) / 0.95
                x_range = new_max - new_min
                if new_min < data_min:
                    new_min = data_min
                    new_max = new_min + x_range
                if new_max > data_max + buffer:
                    new_max = data_max + buffer
                    new_min = new_max - x_range

            self.ax1.set_xlim(new_min, new_max)
            self.ax2.set_xlim(new_min, new_max)
            self.drag_start_x = event.xdata
            self.fig.canvas.draw_idle()

    def on_release(self, event):
        if event.button == 1:
            self.dragging = False
            self.drag_start_x = None

    def zoom(self, event):
        if self.global_df is None or len(self.global_df) < 2:
            return

        base_scale = 1.5
        if event.button == 'up':
            self.zoom_level = max(0.1, self.zoom_level / base_scale)
        elif event.button == 'down':
            self.zoom_level = min(10.0, self.zoom_level * base_scale)
        else:
            return

        x_min, x_max = self.ax1.get_xlim()
        x_mid = (x_min + x_max) / 2
        if self.global_df is not None and len(self.global_df) >= 2:
            data_min = mdates.date2num(self.global_df.index[0])
            data_max = mdates.date2num(self.global_df.index[-1])
            x_range = self.zoom_level * (data_max - data_min) / 0.95
            new_min = max(data_min, x_mid - x_range / 2)
            new_max = min(data_max + 0.05 * x_range / 0.95, x_mid + x_range / 2)
            self.ax1.set_xlim(new_min, new_max)
            self.ax2.set_xlim(new_min, new_max)

        self.update_y_ticks()
        self.fig.canvas.draw()

    def connect_handlers(self):
        self.fig.canvas.mpl_connect('axes_enter_event', self.on_axes_enter)
        self.fig.canvas.mpl_connect('axes_leave_event', self.on_axes_leave)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.fig.canvas.mpl_connect('resize_event', self.on_resize)
        self.fig.canvas.mpl_connect('button_press_event', self.on_press)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.fig.canvas.mpl_connect('button_release_event', self.on_release)
        self.fig.canvas.mpl_connect('scroll_event', self.zoom)

    def init_status_text(self):
        status = "Connected" if self.ib.isConnected() else "Disconnected"
        status_color = 'green' if self.ib.isConnected() else 'red'
        self.status_text = self.fig.text(0.95, 0.05, status, color=status_color, ha='right', va='bottom', fontsize=10)
        self.analysis_text = self.fig.text(0.5, 0.3, "No squeeze", color='white', ha='center', va='bottom', fontsize=10, weight='bold')

    def market_closed_blink(self, frame):
        self.market_closed_blink_state = not self.market_closed_blink_state
        self.analysis_text.set_visible(self.market_closed_blink_state)
        self.analysis_text.set_color(self.current_message_color)
        self.fig.canvas.draw_idle()

    def rapid_blink(self, frame):
        self.analysis_text.set_visible(not self.analysis_text.get_visible())
        self.analysis_text.set_color(self.current_message_color)
        self.fig.canvas.draw_idle()

    def start_rapid_blink(self):
        if self.rapid_blink_anim is not None:
            if hasattr(self.rapid_blink_anim, 'event_source') and self.rapid_blink_anim.event_source is not None:
                self.rapid_blink_anim.event_source.stop()
            self.rapid_blink_anim = None
        self.rapid_blink_anim = FuncAnimation(self.fig, self.rapid_blink, interval=250, frames=20, repeat=False, cache_frame_data=False)
        def make_steady():
            self.analysis_text.set_visible(True)
            self.analysis_text.set_color(self.current_message_color)
            if self.last_analysis_message.startswith("Market Closed"):
                if self.market_closed_anim is not None:
                    if hasattr(self.market_closed_anim, 'event_source') and self.market_closed_anim.event_source is not None:
                        self.market_closed_anim.event_source.stop()
                    self.market_closed_anim = None
                self.market_closed_blink_state = True
                self.analysis_text.set_visible(True)
                self.market_closed_anim = FuncAnimation(self.fig, self.market_closed_blink, interval=2000, repeat=True, cache_frame_data=False)
            self.fig.canvas.draw_idle()
        self.rapid_blink_anim.event_source.add_callback(make_steady)

    def is_market_closed(self):
        et_tz = pytz.timezone('US/Eastern')
        now = datetime.now(pytz.UTC).astimezone(et_tz)
        day = now.weekday()
        hour = now.hour
        minute = now.minute

        regular_closure = False
        if day == 6:
            if hour < 18:
                regular_closure = True
        elif day == 5:
            if hour >= 17:
                regular_closure = True
        elif day == 4 and hour == 23 and minute >= 45:
            regular_closure = True
        else:
            if hour == 17 or (hour == 18 and minute < 0):
                regular_closure = True

        holiday_closure = now.date() in self.us_holidays

        return holiday_closure or regular_closure

    def get_next_market_opening(self):
        et_tz = pytz.timezone('US/Eastern')
        now = datetime.now(pytz.UTC).astimezone(et_tz)
        day = now.weekday()
        hour = now.hour
        minute = now.minute

        next_open = None

        if now.date() in self.us_holidays:
            next_open = now + timedelta(days=1)
            while next_open.date() in self.us_holidays or next_open.weekday() in (5, 6):
                next_open += timedelta(days=1)
            next_open = next_open.replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            if day == 6:
                if hour < 18:
                    next_open = now.replace(hour=18, minute=0, second=0, microsecond=0)
            elif day == 5:
                if hour >= 17:
                    next_open = now + timedelta(days=(6 - day))
                    while next_open.date() in self.us_holidays:
                        next_open += timedelta(days=1)
                    next_open = next_open.replace(hour=18, minute=0, second=0, microsecond=0)
            elif day == 4:
                if hour == 23 and minute >= 45:
                    next_open = now + timedelta(days=1)
                    next_open = next_open.replace(hour=0, minute=0, second=0, microsecond=0)
                    while next_open.date() in self.us_holidays:
                        next_open += timedelta(days=1)
                    if next_open.weekday() == 5:
                        next_open += timedelta(days=1)
                        next_open = next_open.replace(hour=18, minute=0, second=0, microsecond=0)
            else:
                if hour == 17 or (hour == 18 and minute < 0):
                    next_open = now.replace(hour=18, minute=0, second=0, microsecond=0)
                else:
                    return None, None

        if next_open is None:
            return None, None

        if next_open <= now:
            next_open += timedelta(days=1)

        time_diff = next_open - now
        total_seconds = int(time_diff.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return hours, minutes

    def update(self, frame):
        try:
            if self.initial_skip_counter > 0:
                self.initial_skip_counter -= 1
                status = "Connecting..."
                status_color = 'yellow'
                self.status_text.set_text(status)
                self.status_text.set_color(status_color)
                self.fig.canvas.draw_idle()
                return

            if not self.ib.isConnected() or not self.check_api_health():
                status = "Disconnected"
                status_color = 'red'
                self.status_text.set_text(status)
                self.status_text.set_color(status_color)
                self.fig.canvas.draw_idle()
                if not self.dialog_active:
                    self.dialog_active = True
                    response = self.show_disconnect_dialog()
                    if response:
                        try:
                            connected = self.connect_ib()
                            while not connected:
                                self.show_warning_popup("Connection Warning", "Failed to reconnect to IBKR API. Retry or Cancel?")
                                response = self.show_disconnect_dialog()
                                if response:
                                    connected = self.connect_ib()
                                else:
                                    self.cleanup()
                                    plt.close('all')
                                    if self.tk_root is not None:
                                        self.tk_root.destroy()
                                    sys.exit()
                        except Exception:
                            self.show_warning_popup("Reconnect Error", "Failed to reconnect. Please try restarting the program.")
                            self.cleanup()
                            plt.close('all')
                            if self.tk_root is not None:
                                self.tk_root.destroy()
                            sys.exit()
                    else:
                        self.cleanup()
                        plt.close('all')
                        if self.tk_root is not None:
                            self.tk_root.destroy()
                        sys.exit()
                    self.dialog_active = False
                return

            status = "Connected"
            status_color = 'green'
            self.status_text.set_text(status)
            self.status_text.set_color(status_color)

            try:
                end_time = datetime.now().strftime('%Y%m%d-%H:%M:%S')
                bars = self.ib.reqHistoricalData(
                    self.es,
                    endDateTime=end_time,
                    durationStr='1 D',
                    barSizeSetting='3 mins',
                    whatToShow='TRADES',
                    useRTH=False,
                    formatDate=1
                )
            except (ConnectionError, Exception):
                status = "Data Error"
                status_color = 'red'
                self.status_text.set_text(status)
                self.status_text.set_color(status_color)
                self.fig.canvas.draw_idle()
                return

            if not bars:
                if self.global_df is None or self.global_df.empty:
                    new_message = "No Data"
                    new_color = 'white'
                    self.analysis_text.set_text(new_message)
                    self.analysis_text.set_color(new_color)
                    self.current_message_color = new_color
                    self.fig.canvas.draw()


def setup_realtime_bars(self):
    try:
        if hasattr(self, 'realtime_bar_data'):
            self.ib.cancelRealTimeBars(self.realtime_bar_data)
        self.realtime_bar_data = self.ib.reqRealTimeBars(
            self.es,
            barSize=5,
            whatToShow='TRADES',
            useRTH=False
        )
        self.ib.pendingTickersEvent += self.on_realtime_bar
        print("[DEBUG] Real-time bars subscription started.")
    except Exception as e:
        print(f"[ERROR] Failed to start real-time bars: {e}")

def on_realtime_bar(self, tickers):
    for ticker in tickers:
        if ticker.contract.conId != self.es.conId:
            continue
        bar_time = ticker.time.replace(tzinfo=None)
        new_row = {
            'open': ticker.open,
            'high': ticker.high,
            'low': ticker.low,
            'close': ticker.close,
            'volume': ticker.volume
        }
        new_df = pd.DataFrame([new_row], index=[bar_time])
        if self.global_df is None:
            self.global_df = new_df
        else:
            self.global_df = pd.concat([self.global_df, new_df]).drop_duplicates().sort_index()
        print(f"[DEBUG] New real-time bar: {bar_time}, Close: {ticker.close}")
        self.update_plot()

                    return
            else:
                new_df = util.df(bars)
                if new_df is None or new_df.empty:
                    if self.global_df is None or self.global_df.empty:
                        new_message = "No Data"
                        new_color = 'white'
                        self.analysis_text.set_text(new_message)
                        self.analysis_text.set_color(new_color)
                        self.current_message_color = new_color
                        self.fig.canvas.draw()
                        return
                else:
                    new_df.set_index('date', inplace=True)
                    new_df.index = new_df.index.tz_localize(None)
                    if self.global_df is None:
                        self.global_df = new_df
                    else:
                        self.global_df = pd.concat([self.global_df, new_df]).drop_duplicates().sort_index()
                        cutoff = pd.to_datetime(end_time).tz_localize(None) - pd.Timedelta(days=2)
                        self.global_df = self.global_df[self.global_df.index >= cutoff]

            self.global_df.ffill(inplace=True)
            df = self.global_df.copy()

            market_closed = self.is_market_closed()
            market_message = None
            if self.global_df is not None and not self.global_df.empty:
                et_tz = pytz.timezone('US/Eastern')
                now = datetime.now(pytz.UTC).astimezone(et_tz)
                now_naive = now.replace(tzinfo=None)
                latest_time = self.global_df.index[-1]
                time_diff = (now_naive - latest_time).total_seconds()
                if time_diff > 300:
                    market_closed = True

            if market_closed:
                hours, minutes = self.get_next_market_opening()
                if hours is not None and minutes is not None:
                    market_message = f"Market Closed - Reopens in {hours:02d}:{minutes:02d}"

            if len(df) < 20:
                new_message = market_message if market_closed else "No squeeze"
                new_color = 'white'
                self.analysis_text.set_text(new_message)
                self.analysis_text.set_color(new_color)
                self.current_message_color = new_color
                message_changed = self.last_analysis_message != new_message
                self.last_analysis_message = new_message
                if self.market_closed_anim is not None:
                    if hasattr(self.market_closed_anim, 'event_source') and self.market_closed_anim.event_source is not None:
                        self.market_closed_anim.event_source.stop()
                    self.market_closed_anim = None
                if self.rapid_blink_anim is not None:
                    if hasattr(self.rapid_blink_anim, 'event_source') and self.rapid_blink_anim.event_source is not None:
                        self.rapid_blink_anim.event_source.stop()
                    self.rapid_blink_anim = None
                if message_changed:
                    self.start_rapid_blink()
                elif market_message and market_message.startswith("Market Closed"):
                    self.market_closed_blink_state = True
                    self.analysis_text.set_visible(True)
                    self.analysis_text.set_color(new_color)
                    self.market_closed_anim = FuncAnimation(self.fig, self.market_closed_blink, interval=2000, repeat=True, cache_frame_data=False)
                else:
                    self.analysis_text.set_visible(True)
                    self.analysis_text.set_color(new_color)
                self.fig.canvas.draw()
                return

            sma = df['close'].rolling(window=20).mean()
            std = df['close'].rolling(window=20).std()
            df['upper_bb'] = sma + 2 * std
            df['lower_bb'] = sma - 2 * std

            ema = df['close'].ewm(span=20, adjust=False).mean()
            df['tr'] = np.maximum(df['high'] - df['low'],
                                  np.maximum(abs(df['high'] - df['close'].shift()),
                                             abs(df['low'] - df['close'].shift())))
            df['atr'] = df['tr'].rolling(window=20).mean()
            df['upper_kc'] = ema + 1.5 * df['atr']
            df['lower_kc'] = ema - 1.5 * df['atr']

            df['squeeze_on'] = (df['lower_bb'] > df['lower_kc']) & (df['upper_bb'] < df['upper_kc'])

            df['momentum'] = df['close'] - df['close'].shift(20)
            df['momentum'] = df['momentum'].fillna(0)

            self.ax1.clear()
            self.ax2.clear()

            self.ax1.set_facecolor('#D3D3D3')
            self.ax2.set_facecolor('#D3D3D3')

            self.ax1.plot(df.index, df['close'], color='black', linestyle='-', linewidth=1)
            self.ax1.plot(df.index, df['upper_bb'], color='darkblue')
            self.ax1.plot(df.index, df['lower_bb'], color='darkblue')
            self.ax1.plot(df.index, df['upper_kc'], color='magenta')
            self.ax1.plot(df.index, df['lower_kc'], color='magenta')
            self.ax1.fill_between(df.index, df['lower_bb'], df['upper_bb'], where=df['squeeze_on'],
                                 color='gold', alpha=0.3)
            self.ax1.grid(which='both', linestyle='--', alpha=0.7)
            self.ax1.set_title('TTM Squeeze', color='white')

            self.ax1.axvline(df.index[-1], color='blue', linestyle='-', linewidth=1)

            bar_colors = np.where(df['momentum'] > 0, 'green', 'red')
            bar_width = (df.index[1] - df.index[0]).total_seconds() / 86400 if len(df) > 1 else 0.001
            self.ax2.bar(df.index, df['momentum'], color=bar_colors, width=bar_width)
            self.ax2.axhline(0, color='black', linestyle='--', linewidth=1)
            self.ax2.set_title('Momentum Oscillator', color='white', loc='right')
            self.ax2.grid(which='both', linestyle='--', alpha=0.7)

            self.ax2.axvline(df.index[-1], color='blue', linestyle='-', linewidth=1)

            self.ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y%m%d %H:%M'))
            self.ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
            plt.setp(self.ax2.xaxis.get_majorticklabels(), fontsize=8, rotation=30, ha='right', color='white')

            price_min = df['close'].min()
            price_max = df['close'].max()
            price_range = price_max - price_min
            buffer = 0.02 * price_range
            self.ax1_ylim = (price_min - buffer, price_max + buffer)
            self.ax1.set_ylim(self.ax1_ylim)

            mom_max = max(abs(df['momentum'].min()), abs(df['momentum'].max()))
            self.ax2.set_ylim(-mom_max * 1.1, mom_max * 1.1)

            self.update_y_ticks()

            new_message = None
            new_color = 'white'
            if market_closed:
                new_message = market_message
                new_color = 'white'
            else:
                if not df['squeeze_on'].iloc[-1]:
                    new_message = "No squeeze"
                    new_color = 'white'
                else:
                    slope = 0
                    try:
                        if len(df) >= 5 and 'volume' in df.columns:
                            last_volumes = df['volume'].iloc[-5:].values
                            x = np.arange(len(last_volumes))
                            slope, _ = np.polyfit(x, last_volumes, 1)
                    except (KeyError, ValueError):
                        pass

                    momentum = df['momentum'].iloc[-1]
                    if momentum > 0 and (slope >= 0 or momentum > 10):
                        new_message = "SQUEEZE - Bullish"
                        new_color = 'green'
                    elif momentum < 0 and (slope <= 0 or momentum < -10):
                        new_message = "SQUEEZE - Bearish"
                        new_color = 'red'
                    else:
                        new_message = "SQUEEZE - Neutral"
                        new_color = 'white'

            message_changed = self.last_analysis_message != new_message
            self.last_analysis_message = new_message
            self.current_message_color = new_color

            self.analysis_text.set_text(new_message)
            self.analysis_text.set_color(new_color)

            if self.market_closed_anim is not None:
                if hasattr(self.market_closed_anim, 'event_source') and self.market_closed_anim.event_source is not None:
                    self.market_closed_anim.event_source.stop()
                self.market_closed_anim = None
            if self.rapid_blink_anim is not None:
                if hasattr(self.rapid_blink_anim, 'event_source') and self.rapid_blink_anim.event_source is not None:
                    self.rapid_blink_anim.event_source.stop()
                self.rapid_blink_anim = None

            if message_changed:
                self.start_rapid_blink()
            elif market_message and market_message.startswith("Market Closed"):
                self.market_closed_blink_state = True
                self.analysis_text.set_visible(True)
                self.analysis_text.set_color(new_color)
                self.market_closed_anim = FuncAnimation(self.fig, self.market_closed_blink, interval=2000, repeat=True, cache_frame_data=False)
            else:
                self.analysis_text.set_visible(True)
                self.analysis_text.set_color(new_color)

            if self.is_crosshair_visible and self.last_mouse_xdata is not None and self.last_mouse_ydata is not None:
                self.crosshair_vline1 = self.ax1.axvline(x=self.last_mouse_xdata, color='red', linewidth=0.5, visible=True)
                self.crosshair_hline1 = self.ax1.axhline(y=self.last_mouse_ydata, color='red', linewidth=0.5, visible=True)
                self.crosshair_vline2 = self.ax2.axvline(x=self.last_mouse_xdata, color='red', linewidth=0.5, visible=True)

            if self.global_df is not None and len(self.global_df) >= 2:
                data_min = mdates.date2num(df.index[0])
                data_max = mdates.date2num(df.index[-1])
                x_range = self.zoom_level * (data_max - data_min) / 0.95
                buffer = 0.05 * x_range / 0.95
                if self.ax1.get_xlim() == (0, 1):
                    self.ax1.set_xlim(data_max - x_range, data_max + buffer)
                    self.ax2.set_xlim(data_max - x_range, data_max + buffer)
                else:
                    x_min, x_max = self.ax1.get_xlim()
                    if x_max < data_max + buffer:
                        self.ax1.set_xlim(x_min, data_max + buffer)
                        self.ax2.set_xlim(x_min, data_max + buffer)

            self.fig.canvas.draw()

        except (ConnectionError, ValueError, KeyError):
            status = "Error"
            self.status_text.set_text(status)
            self.status_text.set_color('red')
            self.fig.canvas.draw_idle()
        except Exception:
            status = "Error"
            self.status_text.set_text(status)
            self.status_text.set_color('red')
            self.fig.canvas.draw_idle()

    def process_ibkr_events(self):
        try:
            if self.ib.isConnected():
                self.ib.sleep(0)
        except Exception:
            pass
        self.fig.canvas.get_tk_widget().after(500, self.process_ibkr_events)

    def cleanup(self):
        if self.animation is not None:
            if hasattr(self.animation, 'event_source') and self.animation.event_source is not None:
                self.animation.event_source.stop()
            self.animation = None
        if self.market_closed_anim is not None:
            if hasattr(self.market_closed_anim, 'event_source') and self.market_closed_anim.event_source is not None:
                self.market_closed_anim.event_source.stop()
            self.market_closed_anim = None
        if self.rapid_blink_anim is not None:
            if hasattr(self.rapid_blink_anim, 'event_source') and self.rapid_blink_anim.event_source is not None:
                self.rapid_blink_anim.event_source.stop()
            self.rapid_blink_anim = None
        if self.ib.isConnected():
            self.ib.disconnect()
        plt.close('all')
        if self.tk_root is not None:
            self.tk_root.destroy()

    def run(self):
        connected = self.connect_ib()
        while not connected:
            response = self.show_connection_popup()
            if response:
                connected = self.connect_ib()
            else:
                self.cleanup()
                sys.exit()

        try:
            self.ib.qualifyContracts(self.es)
        self.setup_realtime_bars()
        self.setup_realtime_bars()
        except Exception:
            self.show_warning_popup("Contract Error", f"Failed to qualify contract: {self.es}. Please check the contract details.")
            self.cleanup()
            sys.exit()

        self.ib.errorEvent += self.on_error
        atexit.register(self.cleanup)

        self.setup_plot()
        self.setup_text_boxes()
        self.connect_handlers()
        self.init_status_text()

        self.fig.canvas.draw()
        plt.pause(1.0)

        self.animation = FuncAnimation(self.fig, self.update, interval=5000, cache_frame_data=False)

        self.fig.canvas.get_tk_widget().after(500, self.process_ibkr_events)

        while True:
            try:
                self.fig.canvas.flush_events()
                plt.pause(0.1)
                if self.tk_root is not None:
                    self.tk_root.update()
            except tk.TclError:
                break
            except KeyboardInterrupt:
                break
            except Exception:
                break

def main():
    try:
        app = TTMSqueeze()
        app.run()
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        plt.close('all')

if __name__ == "__main__":
    main()

def update_plot(self):
    if self.global_df is None or self.global_df.empty:
        return
    df = self.global_df.copy()
    df = df.tail(100)
    df['momentum'] = df['close'] - df['close'].shift(5)
    df['momentum'] = df['momentum'].fillna(0)

    self.ax1.clear()
    self.ax2.clear()
    self.ax1.set_facecolor('#D3D3D3')
    self.ax2.set_facecolor('#D3D3D3')

    self.ax1.plot(df.index, df['close'], color='black')
    self.ax2.bar(df.index, df['momentum'], color='green')
    self.ax2.axhline(0, color='black', linestyle='--')

    self.fig.canvas.draw_idle()
