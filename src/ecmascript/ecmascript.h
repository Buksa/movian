#pragma once

#include "ext/duktape/duktape.h"
#include "misc/queue.h"
#include "arch/threads.h"
#include "arch/atomic.h"

struct es_resource;
struct rstr;

LIST_HEAD(es_resource_list, es_resource);
LIST_HEAD(es_context_list, es_context);


/**
 *
 */
typedef struct es_context {
  LIST_ENTRY(es_context) ec_link;
  char *ec_id;

  int ec_refcount;

  hts_mutex_t ec_mutex;
  duk_context *ec_duk;
  struct es_resource_list ec_resources;
} es_context_t;


/**
 *
 */
typedef struct es_resource_class {
  const char *erc_name;
  size_t erc_size;

  void (*erc_destroy)(struct es_resource *er);

} es_resource_class_t;


/**
 *
 */
typedef struct es_resource {
  LIST_ENTRY(es_resource) er_link;
  const es_resource_class_t *er_class;
  es_context_t *er_ctx;
  int er_refcount;

} es_resource_t;


es_context_t *es_get(duk_context *ctx);

void es_dumpstack(duk_context *ctx);

void es_dump_err(duk_context *ctx);

/**
 * Resources
 */
static inline void es_resource_retain(es_resource_t *er)
{
  atomic_add(&er->er_refcount, 1);
}

void es_resource_release(es_resource_t *er);

void es_resource_unlink(es_resource_t *er);

static inline void es_resource_destroy(es_resource_t *er)
{
  er->er_class->erc_destroy(er);
}

void *es_resource_alloc(const es_resource_class_t *erc);

void es_resource_init(es_resource_t *er, es_context_t *ec);

static inline void *es_resource_create(es_context_t *ec,
                                       const es_resource_class_t *erc)
{
  void *r = es_resource_alloc(erc);
  es_resource_init(r, ec);
  return r;
}


/**
 * Contexts
 */
static inline es_context_t * __attribute__ ((warn_unused_result))
es_context_retain(es_context_t *ec)
{
  atomic_add(&ec->ec_refcount, 1);
  return ec;
}

void es_context_release(es_context_t *ec);

void es_context_begin(es_context_t *ec);

void es_context_end(es_context_t *ec);


/**
 * Plugin interface
 */
int ecmascript_plugin_load(const char *id, const char *fullpath,
                           char *errbuf, size_t errlen);

void ecmascript_plugin_unload(const char *id);


/**
 * Misc support
 */

int es_prop_is_true(duk_context *ctx, int obj_idx, const char *id);

int es_prop_to_int(duk_context *ctx, int obj_idx, const char *id, int def);

struct rstr *es_prop_to_rstr(duk_context *ctx, int obj_idx, const char *id);


/**
 * Function definitions
 */

extern const duk_function_list_entry fnlist_Showtime_service[];
extern const duk_function_list_entry fnlist_Showtime_page[];
extern const duk_function_list_entry fnlist_Showtime_prop[];
extern const duk_function_list_entry fnlist_Showtime_io[];
extern const duk_function_list_entry fnlist_Showtime_string[];
extern const duk_function_list_entry fnlist_Showtime_htsmsg[];
extern const duk_function_list_entry fnlist_Showtime_metadata[];