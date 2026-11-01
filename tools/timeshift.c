/* timeshift: a dyld interposer that moves the wall clock by TIMESHIFT_SECONDS
 * (negative = into the past) for one process tree. Used to run a 2002 FLEXlm-
 * licensed compiler whose evaluation feature expired; nothing is patched. */
#include <sys/time.h>
#include <time.h>
#include <stdlib.h>
#include <mach/mach_time.h>

static long shift(void) {
    static long s; static int init;
    if (!init) { const char *e = getenv("TIMESHIFT_SECONDS"); s = e ? atol(e) : 0; init = 1; }
    return s;
}
static int my_gettimeofday(struct timeval *tv, void *tz) {
    int r = gettimeofday(tv, tz);
    if (r == 0 && tv) tv->tv_sec += shift();
    return r;
}
static int my_clock_gettime(clockid_t id, struct timespec *ts) {
    int r = clock_gettime(id, ts);
    if (r == 0 && ts && (id == CLOCK_REALTIME)) ts->tv_sec += shift();
    return r;
}
static time_t my_time(time_t *t) {
    time_t v = time(NULL) + shift();
    if (t) *t = v;
    return v;
}
#define INTERPOSE(new, old) __attribute__((used)) static struct { const void *n, *o; } \
    _interpose_##old __attribute__((section("__DATA,__interpose"))) = { (const void *)(new), (const void *)(old) }
INTERPOSE(my_gettimeofday, gettimeofday);
INTERPOSE(my_clock_gettime, clock_gettime);
INTERPOSE(my_time, time);
