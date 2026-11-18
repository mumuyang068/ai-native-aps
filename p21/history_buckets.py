"""Shared local-calendar buckets; flow intervals and balance instants are separate."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo


def instant(value):
    try:
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
        if value.utcoffset() is None:raise ValueError()
        return value
    except (AttributeError,TypeError,ValueError):raise ValueError('HISTORY_TIMEZONE_REQUIRED') from None


def coverage_end(scope,grain):
    """Query buckets may finish the observed calendar period, not invent history.

    source_as_of is an inclusive knowledge instant; query end stays exclusive.
    When the capture ends at that instant, allow its containing final bucket so
    an event exactly at the known endpoint can be represented. An explicitly
    shorter capture retains its original bound.
    """
    if grain not in {'day','week','month'}:raise ValueError('HISTORY_GRAIN_UNSUPPORTED')
    end=instant(scope['end'])
    if end!=instant(scope['source_as_of']):return end
    point=end.astimezone(ZoneInfo(scope['timezone'])).replace(hour=0,minute=0,second=0,microsecond=0)
    if grain=='week':point-=timedelta(days=point.weekday())
    if grain=='month':
        point=point.replace(day=1)
        return point.replace(year=point.year+1,month=1) if point.month==12 else point.replace(month=point.month+1)
    return point+timedelta(days=7 if grain=='week' else 1)


def check_coverage(scope,start,end,grain):
    if (instant(start)<instant(scope['origin']) or instant(start)>instant(scope['end'])
        or instant(end)>coverage_end(scope,grain)):
        raise ValueError('HISTORY_OUTSIDE_SOURCE_COVERAGE')


def buckets(start,end,grain,timezone):
    if grain not in {'day','week','month'}:raise ValueError('HISTORY_GRAIN_UNSUPPORTED')
    zone=ZoneInfo(timezone);a=instant(start).astimezone(zone);z=instant(end).astimezone(zone)
    if not a<z or z-a>timedelta(days=3660):raise ValueError('HISTORY_RANGE_INVALID')
    point=a.replace(hour=0,minute=0,second=0,microsecond=0)
    if grain=='week':point-=timedelta(days=point.weekday())
    if grain=='month':point=point.replace(day=1)
    out=[]
    while point<z:
        following=(point.replace(year=point.year+1,month=1) if point.month==12 else point.replace(month=point.month+1)) if grain=='month' else point+timedelta(days=7 if grain=='week' else 1)
        lo,hi=max(a,point),min(z,following)
        out.append(dict(id=point.isoformat(),label=point.strftime('%Y-%m' if grain=='month' else '%m-%d'),
            start=lo.isoformat(),end=hi.isoformat(),partial=lo!=point or hi!=following))
        point=following
    return out
