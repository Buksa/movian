/*
 *  Copyright (C) 2007-2015 Lonelycoder AB
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 *  This program is also available under a commercial proprietary license.
 *  For more information, contact andreas@lonelycoder.com
 */
#ifndef FA_ZIP_PATH_H__
#define FA_ZIP_PATH_H__

/**
 * Resolve exact . and .. components in a ZIP member path.
 *
 * Paths without dot components are returned unchanged and do not allocate.
 * A normalized result is malloc'ed and returned through allocated. NULL means
 * allocation failure or an attempt to climb above the archive root.
 */
const char *fa_zip_resolve_member_path(const char *path, char **allocated);

#endif /* FA_ZIP_PATH_H__ */
