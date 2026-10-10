#!/usr/bin/env python3
"""Patch Metadata/project_settings.config inside a 3MF: change keys and, if
needed, drop a filament from the project entirely.

Everything else in the archive is carried over verbatim — mesh, paint,
preview, plate layout. That is how EXACTLY one change gets tested at a time.

    python3 tools/patch3mf.py in.3mf out.3mf key=value [key=value ...]
    python3 tools/patch3mf.py in.3mf out.3mf --drop-filament 2           # 1-based
    python3 tools/patch3mf.py in.3mf out.3mf --drop-filament 2 --into 1

Dropping a filament renumbers everything that points at filaments by number:
object and part extruders, plate filament maps, paint on the faces, filament
changes by layer, the *_filament keys of the process. A filament still in use
is refused; `--into M` (numbered as in the input) hands its uses to M.
"""
import json, pathlib, re, sys, zipfile

CFG = 'Metadata/project_settings.config'
MODEL_CFG = 'Metadata/model_settings.config'
LAYER_CFG = 'Metadata/custom_gcode_per_layer.xml'
PROFILES = pathlib.Path('/Applications/BambuStudio.app/Contents/Resources/profiles/BBL')

# Which lists are per filament is decided by name, never by length: printable_area
# has 4 corners, machine_max_* 2 entries, wipe_tower_x one per plate - and any of
# them collides with the filament count sooner or later. Cut one and the CLI
# exits 0 without writing G-code.
#
# The keys of a filament preset as Bambu Studio ships them; an installed Bambu
# Studio adds whatever its own presets carry on top.
FILAMENT_PRESET_KEYS = set('''
activate_air_filtration additional_cooling_fan_speed additional_fan_full_speed_layer
chamber_temperatures circle_compensation_speed close_additional_fan_first_x_layers
close_fan_the_first_x_layers complete_print_exhaust_fan_speed cool_plate_temp
cool_plate_temp_initial_layer cooling_perimeter_transition_distance cooling_slowdown_logic
counter_coef_1 counter_coef_2 counter_coef_3 counter_limit_max counter_limit_min
diameter_limit during_print_exhaust_fan_speed eng_plate_temp eng_plate_temp_initial_layer
fan_cooling_layer_time fan_max_speed fan_min_speed filament_adaptive_volumetric_speed
filament_adhesiveness_category filament_bridge_speed filament_change_length
filament_change_length_nc filament_cooling_before_tower filament_cost filament_density
filament_deretraction_speed filament_dev_ams_drying_ams_limitations
filament_dev_ams_drying_heat_distortion_temperature filament_dev_ams_drying_temperature
filament_dev_ams_drying_time filament_dev_chamber_drying_bed_temperature
filament_dev_chamber_drying_time filament_dev_drying_cooling_temperature
filament_dev_drying_softening_temperature filament_diameter filament_enable_overhang_speed
filament_end_gcode filament_extruder_compatibility filament_extruder_id
filament_extruder_variant filament_flow_ratio filament_flush_temp filament_flush_temp_fast
filament_flush_volumetric_speed filament_is_support filament_long_retractions_when_cut
filament_long_retractions_when_ec filament_max_volumetric_speed filament_metal_stickiness
filament_minimal_purge_on_wipe_tower filament_overhang_1_4_speed filament_overhang_2_4_speed
filament_overhang_3_4_speed filament_overhang_4_4_speed filament_overhang_totally_speed
filament_pre_cooling_temperature filament_pre_cooling_temperature_nc
filament_preheat_temperature_delta filament_prime_volume filament_prime_volume_nc
filament_printable filament_ramming_travel_time filament_ramming_travel_time_nc
filament_ramming_volumetric_speed filament_ramming_volumetric_speed_nc
filament_retract_before_wipe filament_retract_length_nc filament_retract_restart_extra
filament_retract_when_changing_layer filament_retraction_distances_when_cut
filament_retraction_distances_when_ec filament_retraction_length
filament_retraction_minimum_travel filament_retraction_speed filament_scarf_gap
filament_scarf_height filament_scarf_length filament_scarf_seam_type filament_settings_id
filament_shrink filament_soluble filament_start_gcode filament_support_printable
filament_tower_interface_pre_extrusion_dist filament_tower_interface_pre_extrusion_length
filament_tower_interface_print_temp filament_tower_interface_purge_volume
filament_tower_ironing_area filament_type filament_velocity_adaptation_factor
filament_vendor filament_wipe filament_wipe_distance filament_z_hop filament_z_hop_types
fins_extrude_safe_temp first_x_layer_fan_speed full_fan_speed_layer hole_coef_1
hole_coef_2 hole_coef_3 hole_limit_max hole_limit_min hot_plate_temp
hot_plate_temp_initial_layer impact_strength_z long_retractions_when_ec
no_slow_down_for_cooling_on_outwalls nozzle_temperature nozzle_temperature_initial_layer
nozzle_temperature_range_high nozzle_temperature_range_low overhang_fan_speed
overhang_fan_threshold override_process_overhang_speed pre_start_fan_time
reduce_fan_stop_start_freq required_nozzle_HRC retraction_distances_when_ec
slow_down_for_layer_cooling slow_down_layer_time slow_down_min_speed
supertack_plate_temp supertack_plate_temp_initial_layer temperature_vitrification
textured_plate_temp textured_plate_temp_initial_layer volumetric_speed_coefficients
'''.split())
# Per filament in a project, but in no preset: the GUI writes them itself.
FILAMENT_PROJECT_KEYS = set('''
default_filament_colour enable_overhang_bridge_fan enable_pressure_advance filament_colour
filament_colour_type filament_ids filament_is_mixed filament_map filament_mixed_components
filament_mixed_gradient filament_mixed_gradient_curve filament_mixed_gradient_per_part
filament_mixed_gradient_range filament_mixed_sublayer_ratios filament_multi_colour
filament_nozzle_map filament_self_index filament_volume_map first_x_layer_part_fan_speed
flush_volumes_vector ironing_fan_speed overhang_threshold_participating_cooling
pressure_advance
'''.split())
# Keys of a preset dump that are not settings at all.
PRESET_META = {'type', 'from', 'name', 'inherits', 'instantiation', 'setting_id', 'include',
               'description', 'version', 'is_custom_defined', 'filament_id',
               'compatible_printers', 'compatible_printers_condition',
               'filament_ingredients_safe', 'filament_emission_safe', 'filament_contact_safe'}
# Laid out as [process, filament 1..N, printer].
FRAMED_KEYS = ('different_settings_to_system', 'inherits_group')
# Keys whose value is a filament number, 0 meaning "the object's own".
NUMBERED = r'(?:extruder|(?:wall|sparse_infill|solid_infill|support|support_interface)_filament)'
NUMBER_KEY = re.compile(r'^(?:wall|sparse_infill|solid_infill|support|support_interface|'
                        r'wipe_tower)_filament$')


def filament_keys():
    keys = FILAMENT_PRESET_KEYS | FILAMENT_PROJECT_KEYS
    for p in (PROFILES / 'filament').rglob('*.json') if PROFILES.is_dir() else ():
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get('type') == 'filament':
            keys |= set(d)
    return keys - PRESET_META


# --------------------------------------------------------------- paint codes
# A paint_color code is a bit stream packed into nibbles, low bit first, the
# nibbles written back to front. A node is 2 bits "how many sides are split"
# and 2 bits "which side", then its children; a leaf is 00 and a state in
# 2 bits, or 11 and 4 more bits for states from 3 up. State 0 is the object's
# extruder, N is filament N. Only the states change, so the order of children
# does not matter here.

def paint_tree(code):
    bits = []
    for ch in reversed(code):
        v = int(ch, 16)
        bits += [(v >> i) & 1 for i in range(4)]
    pos = [0]

    def take(n):
        i = pos[0]
        if i + n > len(bits):
            raise ValueError(f'код покраски {code}: поток кончился раньше времени')
        pos[0] += n
        return sum(bits[i + k] << k for k in range(n))

    def rec():
        sides = take(2)
        if sides == 0:
            st = take(2)
            return ('leaf', 3 + take(4) if st == 3 else st)
        special = take(2)
        return ('node', sides, special, [rec() for _ in range(sides + 1)])

    return rec()


def paint_code(tree):
    bits = []

    def put(v, n):
        bits.extend((v >> k) & 1 for k in range(n))

    def rec(t):
        if t[0] == 'leaf':
            put(0, 2)
            if t[1] < 3:
                put(t[1], 2)
            else:
                put(3, 2); put(t[1] - 3, 4)
            return
        put(t[1], 2); put(t[2], 2)
        for c in t[3]:
            rec(c)

    rec(tree)
    bits += [0] * (-len(bits) % 4)
    nib = [sum(bits[i + k] << k for k in range(4)) for i in range(0, len(bits), 4)]
    return ''.join('%X' % v for v in reversed(nib))


def leaves(t):
    if t[0] == 'leaf':
        yield t[1]
    else:
        for c in t[3]:
            yield from leaves(c)


def remap_tree(t, fmap):
    if t[0] == 'leaf':
        return ('leaf', fmap.get(t[1], t[1]) if t[1] else 0)
    return t[:3] + ([remap_tree(c, fmap) for c in t[3]],)


# ---------------------------------------------------------------- drop
def drop_settings(cfg, n, d):
    """Cut filament d (0-based) out of every per-filament value of the config."""
    fk = filament_keys()
    cut, odd = [], []
    for k, v in list(cfg.items()):
        if not isinstance(v, list) or not v:
            continue
        if k in FRAMED_KEYS:
            if len(v) == n + 2:
                cfg[k] = v[:d + 1] + v[d + 2:]; cut.append(k)
            else:
                odd.append(f'{k}[{len(v)}]')
        elif k == 'flush_volumes_matrix':
            if len(v) % (n * n):
                odd.append(f'{k}[{len(v)}]'); continue
            # n x n per extruder, the blocks one after another
            cfg[k] = [v[e * n * n + r * n + c] for e in range(len(v) // (n * n))
                      for r in range(n) for c in range(n) if r != d and c != d]
            cut.append(k)
        elif k in fk:
            if len(v) % n:
                odd.append(f'{k}[{len(v)}]'); continue
            b = len(v) // n
            cfg[k] = v[:d * b] + v[(d + 1) * b:]; cut.append(k)
    if 'filament_self_index' in cfg:
        cfg['filament_self_index'] = [str(i + 1) for i in range(n - 1)]
    return cut, odd


def renumber(v, fmap):
    v = int(v)
    return str(fmap.get(v, v) if v else 0)


def drop_model(text, fmap, d):
    """model_settings.config: extruders, *_filament overrides, plate filament maps."""
    text = re.sub(rf'(<metadata key="{NUMBERED}" value=")(\d+)(")',
                  lambda m: m.group(1) + renumber(m.group(2), fmap) + m.group(3), text)

    def plate_list(m):
        vals = m.group(2).split()
        if len(vals) > d:
            del vals[d]
        return m.group(1) + ' '.join(vals) + m.group(3)
    return re.sub(r'(<metadata key="(?:filament_maps|filament_volume_maps)" value=")([^"]*)(")',
                  plate_list, text)


def users_in_model(text, num):
    """Objects and parts whose own filament is num."""
    names, cur = [], ''
    for m in re.finditer(rf'<(object|part)\b|<metadata key="(name|{NUMBERED})" value="([^"]*)"',
                         text):
        if m.group(1):
            cur = ''
        elif m.group(2) == 'name':
            cur = m.group(3)
        elif m.group(3).isdigit() and int(m.group(3)) == num:
            names.append(f'«{cur or "?"}»' + ('' if m.group(2) == 'extruder'
                                             else f' ({m.group(2)})'))
    return names


def drop_filament(data, cfg, drop, into):
    """Drop filament `drop` (1-based) from the whole archive. -> exit code."""
    n = len(cfg['filament_colour'])
    if n < 2:
        print('в проекте один филамент, выкидывать нечего'); return 2
    if not 1 <= drop <= n:
        print(f'филамента {drop} нет: в проекте их {n}'); return 2
    if into is not None and (not 1 <= into <= n or into == drop):
        print(f'--into {into}: нужен другой филамент из 1..{n}'); return 2
    d = drop - 1
    # old number -> new number; the dropped one goes to --into, or nowhere
    fmap = {j: j - (j > drop) for j in range(1, n + 1) if j != drop}
    if into is not None:
        fmap[drop] = fmap[into]

    ms = data.get(MODEL_CFG, b'').decode('utf-8')
    layer = data.get(LAYER_CFG, b'').decode('utf-8')
    models = [nm for nm in data if nm.startswith('3D/') and nm.endswith('.model')]
    trees, painted = {}, 0
    for nm in models:
        for code in set(re.findall(rb'paint_color="([0-9A-Fa-f]+)"', data[nm])):
            t = trees.setdefault(code, paint_tree(code.decode()))
            if drop in leaves(t):
                painted += data[nm].count(b'paint_color="' + code + b'"')

    users = [f'объект или деталь {u}' for u in users_in_model(ms, drop)]
    users += [f'{k} = {drop}' for k, v in cfg.items()
              if NUMBER_KEY.match(k) and str(v) == str(drop)]
    if painted:
        users.append(f'покраска: {painted} граней')
    nl = len(re.findall(rf'<layer\b[^>]*\bextruder="{drop}"', layer))
    if nl:
        users.append(f'смена филамента по слоям: {nl}')
    if users and into is None:
        print(f'филамент {drop} ещё в работе, выкинуть его — значит перекрасить модель:')
        for u in users:
            print('  ' + u)
        print(f'чем его заменить, задаёт --into M: например --drop-filament {drop} --into 1')
        return 1

    cut, odd = drop_settings(cfg, n, d)
    for k, v in cfg.items():
        if NUMBER_KEY.match(k) and str(v).isdigit():
            cfg[k] = renumber(v, fmap)
    if odd:
        print('не тронуты, длина не ложится на число филаментов: ' + ', '.join(odd))
    if ms:
        data[MODEL_CFG] = drop_model(ms, fmap, d).encode('utf-8')
    if layer:
        data[LAYER_CFG] = re.sub(
            r'(<layer\b[^>]*\bextruder=")(\d+)(")',
            lambda m: m.group(1) + renumber(m.group(2), fmap) + m.group(3), layer).encode('utf-8')
    recoded = {code: paint_code(remap_tree(t, fmap)).encode() for code, t in trees.items()
               if any(s and fmap.get(s, s) != s for s in leaves(t))}
    if recoded:
        pat = re.compile(rb'paint_color="(' + b'|'.join(map(re.escape, recoded)) + rb')"')
        for nm in models:
            data[nm] = pat.sub(lambda m: b'paint_color="' + recoded[m.group(1)] + b'"', data[nm])
    print(f'филамент {drop} выкинут: было {n}, стало {len(cfg["filament_colour"])}'
          + (f'; его место занял {into}' if into is not None else ''))
    print(f'  списков в настройках урезано {len(cut)}, '
          f'кодов покраски перенумеровано {len(recoded)}')
    return 0


def main(argv):
    if {'-h', '--help'} & set(argv):
        print(__doc__); return 0
    if len(argv) < 3:
        print(__doc__); return 2
    src, dst = argv[0], argv[1]
    args = argv[2:]
    drop = into = None
    kv = {}
    i = 0
    while i < len(args):
        if args[i] == '--drop-filament':
            drop = int(args[i + 1]); i += 2
        elif args[i] == '--into':
            into = int(args[i + 1]); i += 2
        else:
            k, v = args[i].split('=', 1); kv[k] = v; i += 1
    if into is not None and drop is None:
        print('--into задаёт, кому отдать выкинутый филамент; без --drop-filament он лишний')
        return 2

    with zipfile.ZipFile(src) as z:
        items = z.infolist()
        data = {it.filename: z.read(it.filename) for it in items}

    if CFG not in data:
        print(f'{src}: нет {CFG}.\n'
              'Файл собран make_multicolor_3mf.py (он не кладёт настроек)\n'
              'или экспортирован без настроек. Они появляются, когда файл открыт\n'
              'и сохранён в Bambu Studio; перенести их туда потом — retune_project.py')
        return 1
    cfg = json.loads(data[CFG])
    if drop is not None:
        rc = drop_filament(data, cfg, drop, into)
        if rc:
            return rc
    for k, v in kv.items():
        old = cfg.get(k)
        cfg[k] = json.loads(v) if v[:1] in '[{' else v
        print(f'  {k}: {old!r} -> {cfg[k]!r}')

    data[CFG] = json.dumps(cfg, indent=4, ensure_ascii=True,
                           sort_keys=True).encode('utf-8')
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zo:
        for it in items:
            zo.writestr(it, data[it.filename])
    print('записано', dst)

    # The GUI applies only the keys listed in different_settings_to_system on top of
    # the system preset. Inventing that list breaks opening the file, so it is never
    # written here - only reported.
    dss = cfg.get('different_settings_to_system') or ['']
    listed = set(str(dss[0]).split(';'))
    unseen = sorted(k for k in kv if k not in listed)
    if unseen:
        print('интерфейс этих правок не увидит — их нет в different_settings_to_system: '
              + ', '.join(unseen))
        print('  CLI и slice.sh режут с ними; в интерфейсе выставить руками')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
