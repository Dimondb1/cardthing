"""Server-rendered price history chart. No JavaScript needed."""

from datetime import timedelta

WIDTH = 600
HEIGHT = 160


def price_chart(points, days, today):
    """Build what the template needs to draw a step chart.

    ``points`` is [(date, price), ...] oldest first. Returns None when there
    are too few points to be useful.
    """
    if len(points) < 2:
        return None

    start = today - timedelta(days=days - 1)
    values = [float(price) for _, price in points]
    low, high = min(values), max(values)
    padding = (high - low) * 0.15 or high * 0.05 or 1.0
    floor, ceiling = low - padding, high + padding
    span_days = max(days - 1, 1)

    def x(day):
        return (day - start).days / span_days * WIDTH

    def y(value):
        return HEIGHT - (value - floor) / (ceiling - floor) * HEIGHT

    path = []
    previous = None
    for day, price in points:
        px, py = x(day), y(float(price))
        if previous is None or (day - previous).days > 1:
            path.append(f"M{px:.1f},{py:.1f}")
        else:
            path.append(f"H{px:.1f}V{py:.1f}")
        previous = day
    # Hold the last price to the end of its day so a single recent change shows.
    last_day = points[-1][0]
    if last_day == today:
        path.append(f"H{WIDTH:.1f}")

    # Most recent occurrence of the lowest and highest prices.
    low_day, low_price = max((p for p in points if float(p[1]) == low), key=lambda p: p[0])
    high_day, high_price = max((p for p in points if float(p[1]) == high), key=lambda p: p[0])

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "path": " ".join(path),
        "start": start,
        "end": today,
        "low": low_price,
        "low_date": low_day,
        "high": high_price,
        "high_date": high_day,
        "low_y": round(y(low), 1),
        "high_y": round(y(high), 1),
        "low_top_pct": round(y(low) / HEIGHT * 100, 2),
        "high_top_pct": round(y(high) / HEIGHT * 100, 2),
        "low_left_pct": round(x(low_day) / WIDTH * 100, 2),
    }
