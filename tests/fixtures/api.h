/* @title: Fixture API */
#ifndef FIXTURE_API_H
#define FIXTURE_API_H

#include "types.h"

/* create a widget at the given point */
struct widget *widget_create(struct point origin, enum color color);

/* compare two points; returns -1/0/1 */
int point_compare(struct point a, struct point b);

/* a global registry pointer */
extern widget_t *widget_registry;

#endif /* FIXTURE_API_H */
