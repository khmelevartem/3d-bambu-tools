#!/usr/bin/env python3
"""Edit a foreign 3MF **by parts**, without touching a single one of its faces.

A Bambu Studio object may have several parts: a `normal_part` adds volume and a
`negative_part` subtracts it. So a downloaded model's shape can be changed
without recomputing its mesh - and while the mesh is intact, so is the paint:
`paint_color` is bound to a triangle's index, which any boolean loses and a
part does not.

    python3 tools/partedit.py list  project.3mf
    python3 tools/partedit.py apply job.json

The job is JSON with `src`, `dst` and a list of `ops` executed in order:

    {"src": "project.3mf", "dst": "new.3mf", "ops": [
      {"op": "part",   "object": "Right arm", "name": "Channel",
       "subtype": "negative_part",
       "mesh": {"kind": "cylinder", "c": [8.9,-8.1,-1.4], "axis": [-0.69,0.67,-0.28],
                "r": 1.23, "t0": -4.5, "t1": 8.0}},
      {"op": "move",   "from": "Left leg", "to": "Body", "R": [[1,0,0],[0,1,0],[0,0,1]], "T": [0,0,0]},
      {"op": "drop",   "name": "Left leg"},
      {"op": "shift",  "name": "Body", "d": [0,0,-1.2]},
      {"op": "object", "name": "Cigar", "extruder": 2, "plate": 2, "pos": [505,120,0],
       "mesh": {"kind": "cylinder", "c": [0,0,0], "axis": [0,0,1], "r": 1.13, "t0": 0, "t1": 8.7}},
      {"op": "drop_plate", "id": 4}]}

`mesh` is either a primitive (`box` with lo/hi, `cylinder`, `sphere`) or
`{"kind": "npz", "path": "..."}` carrying V and F arrays.

Format rules, checked against the slicer source and confirmed by slicing:

* The part type is the `subtype` attribute on `<part>` in
  `Metadata/model_settings.config`: `normal_part`, `negative_part`,
  `modifier_part`, `support_blocker`, `support_enforcer`. **An unknown string
  is silently read as `normal_part`.**
* `<part id=...>` finds its mesh by MATCHING the id against a component's
  `objectid`, not by position. A part with a foreign id is not ignored but
  becomes a `normal_part` - a failure invisible in the preview.
* **Part order decides**: a negative volume is subtracted only from parts
  listed ABOVE it. New parts are therefore appended at the end of
  `<components>` and at the end of `<object>` in the config.
* **Do not cut a part's bottom with a `negative_part`.** Sink the object
  instead (`shift`); the slicer prints nothing below the bed anyway. A cutter
  box additionally drags the object's bounding box down, and the part looks
  sunken in the GUI.
* A negative part sticking out below the bed widens the bounding box. **Verify
  an edit by slicing, not by the preview** - the preview does not show negative
  parts. Compare the layer profile against the original: first-layer area,
  height, filament changes.
"""

import numpy as np

def box(lo, hi):
    lo=np.asarray(lo,float); hi=np.asarray(hi,float)
    V=np.array([[lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]],
                [lo[0],lo[1],hi[2]],[hi[0],lo[1],hi[2]],[hi[0],hi[1],hi[2]],[lo[0],hi[1],hi[2]]],float)
    F=np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]],np.int64)
    return V,F

def frame(ax):
    ax=np.asarray(ax,float); ax=ax/np.linalg.norm(ax)
    u=np.cross(ax,[0,0,1.0])
    if np.linalg.norm(u)<0.3: u=np.cross(ax,[1.0,0,0])
    u/=np.linalg.norm(u); v=np.cross(ax,u)
    return u,v,ax

def cylinder(c, ax, r, t0, t1, seg=96):
    """A cylinder of radius r along ax, from t0 to t1, measured from point c."""
    u,v,w=frame(ax)
    a=np.linspace(0,2*np.pi,seg,endpoint=False)
    ring=np.outer(np.cos(a),u)+np.outer(np.sin(a),v)
    V=np.vstack([c+t0*w+r*ring, c+t1*w+r*ring, [c+t0*w], [c+t1*w]])
    n=seg; b0=2*n; b1=2*n+1
    F=[]
    for i in range(n):
        j=(i+1)%n
        F += [[i,j,n+j],[i,n+j,n+i]]        # side
        F += [[b0,j,i]]                      # bottom
        F += [[b1,n+i,n+j]]                  # top
    return V, np.array(F,np.int64)

def sphere(c, r, nu=48, nv=24):
    c=np.asarray(c,float)
    th=np.linspace(0,np.pi,nv+1)[1:-1]
    ph=np.linspace(0,2*np.pi,nu,endpoint=False)
    P=[]
    for t in th:
        P.append(np.c_[np.sin(t)*np.cos(ph), np.sin(t)*np.sin(ph), np.full(nu,np.cos(t))])
    V=np.vstack([np.vstack(P)*r+c, c+[0,0,r], c-[0,0,r]])
    nrow=len(th); top=nrow*nu; bot=top+1
    F=[]
    for i in range(nrow-1):
        for j in range(nu):
            a=i*nu+j; b=i*nu+(j+1)%nu; d=(i+1)*nu+j; e=(i+1)*nu+(j+1)%nu
            F += [[a,b,e],[a,e,d]]
    for j in range(nu):
        F.append([top, (j+1)%nu, j])
        F.append([bot, (nrow-1)*nu+j, (nrow-1)*nu+(j+1)%nu])
    F=np.array(F,np.int64)[:,[0,2,1]]
    return V, F

def volume(V,F):
    P=V[F]
    return float(np.einsum('ij,ij->i',P[:,0],np.cross(P[:,1],P[:,2])).sum()/6)

def watertight(V,F):
    E=np.concatenate([F[:,[0,1]],F[:,[1,2]],F[:,[2,0]]])
    E2=np.sort(E,axis=1)
    k=E2[:,0].astype(np.int64)*(1<<32)+E2[:,1]
    u,cnt=np.unique(k,return_counts=True)
    return bool((cnt==2).all())


import json, re, uuid, zipfile, shutil, sys
import numpy as np

NS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
      '<model unit="millimeter" xml:lang="en-US" '
      'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
      'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
      'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
      'requiredextensions="p">\n <metadata name="BambuStudio:3mfVersion">1</metadata>\n')

VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')


def mesh_xml(V, F, oid, code=None):
    out = [NS, f' <resources>\n  <object id="{oid}" p:UUID="{uuid.uuid4()}" type="model">\n'
           '   <mesh>\n    <vertices>\n']
    for x, y, z in V:
        out.append(f'     <vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n')
    out.append('    </vertices>\n    <triangles>\n')
    if code is None or isinstance(code, str):
        pc = [f' paint_color="{code}"' if code else ''] * len(F)
    else:
        pc = [f' paint_color="{c}"' if c else '' for c in code]
    for (a, b, c), p in zip(F, pc):
        out.append(f'     <triangle v1="{a}" v2="{b}" v3="{c}"{p}/>\n')
    out.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n')
    return ''.join(out).encode('utf-8')


def transform_model(raw, R, T):
    """Rewrite vertex coordinates in a finished .model, leaving faces and paint alone."""
    out, pos, n = [], 0, 0
    for m in VERT.finditer(raw):
        p = np.array([float(m.group(1)), float(m.group(2)), float(m.group(3))])
        q = R @ p + T
        out.append(raw[pos:m.start()])
        out.append(f'<vertex x="{q[0]:.9g}" y="{q[1]:.9g}" z="{q[2]:.9g}"/>')
        pos = m.end(); n += 1
    out.append(raw[pos:])
    return ''.join(out), n


class Project:
    def __init__(self, src):
        self.z = zipfile.ZipFile(src)
        self.files = {n: self.z.read(n) for n in self.z.namelist()}
        self.top = self.files['3D/3dmodel.model'].decode()
        self.cfg = self.files['Metadata/model_settings.config'].decode()
        # Ids must continue above everything the project already uses: a project
        # edited before may well hold ids in the hundreds, and a repeated id is
        # not rejected — Bambu Studio then shows one object in place of another.
        used = [int(x) for x in re.findall(r'<object id="(\d+)"', self.top)]
        used += [int(x) for x in re.findall(r'<object id="(\d+)"', self.cfg)]
        used += [int(x) for x in re.findall(r'<part id="(\d+)"', self.cfg)]
        used += [int(x) for x in re.findall(r'objectid="(\d+)"', self.top)]
        self.next_id = max(used, default=99) + 1

    # --- reading ---
    def object_id(self, name):
        for oid, blk in re.findall(r'<object id="(\d+)">(.*?)</object>', self.cfg, re.S):
            if f'key="name" value="{name}"' in blk:
                return oid
        raise KeyError(name)

    def entry_of(self, oid):
        m = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % oid, self.top, re.S)
        return re.search(r'p:path="([^"]+)"', m.group(1)).group(1).lstrip('/')

    # --- writing ---
    def _new_ids(self):
        self.next_id += 2
        return self.next_id - 2, self.next_id - 1     # inner, outer

    def add_mesh_file(self, V, F, code=None):
        """A new 3D/Objects/object_N.model. Returns (path, id inside the file)."""
        n = 1 + max(int(re.search(r'object_(\d+)\.model', p).group(1))
                    for p in self.files if p.startswith('3D/Objects/'))
        path = f'3D/Objects/object_{n}.model'
        inner, _ = self._new_ids()
        self.files[path] = mesh_xml(V, F, inner, code)
        return path, inner

    def add_part(self, obj_name, part_name, path, inner, subtype='normal_part'):
        oid = self.object_id(obj_name)
        comp = (f'<component p:path="/{path}" objectid="{inner}" '
                f'p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
        m = re.search(r'<object id="%s".*?(</components>)' % oid, self.top, re.S)
        self.top = self.top[:m.start(1)] + comp + '\n   ' + self.top[m.start(1):]
        part = (f'    <part id="{inner}" subtype="{subtype}">\n'
                f'      <metadata key="name" value="{part_name}"/>\n'
                f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                f'    </part>\n')
        m = re.search(r'(<object id="%s">.*?)(  </object>)' % oid, self.cfg, re.S)
        self.cfg = self.cfg[:m.end(1)] + part + self.cfg[m.start(2):]

    def add_mesh_part(self, obj_name, part_name, V, F, subtype='normal_part', code=None):
        path, inner = self.add_mesh_file(V, F, code)
        self.add_part(obj_name, part_name, path, inner, subtype)

    def move_object_as_part(self, src_name, dst_name, R, T, part_name=None):
        """Move an object's mesh into another object as a part: vertices go through R,T."""
        oid = self.object_id(src_name)
        ent = self.entry_of(oid)
        raw = self.files[ent].decode()
        new, n = transform_model(raw, R, T)
        inner = int(re.search(r'<object id="(\d+)"', new).group(1))
        n2 = 1 + max(int(re.search(r'object_(\d+)\.model', p).group(1))
                     for p in self.files if p.startswith('3D/Objects/'))
        path = f'3D/Objects/object_{n2}.model'
        newid, _ = self._new_ids()
        new = new.replace(f'<object id="{inner}"', f'<object id="{newid}"', 1)
        self.files[path] = new.encode()
        self.add_part(dst_name, part_name or src_name, path, newid)
        return n

    def shift_object(self, name, d):
        """Shift an object on the bed (and in the assembly): needed when a
                negative_part has cut the bottom away and the part hangs above the plate."""
        oid = self.object_id(name)
        def fix(m):
            v = m.group(2).split()
            for i in range(3):
                v[9 + i] = f'{float(v[9 + i]) + d[i]:.6f}'
            return m.group(1) + ' '.join(v) + '"'
        self.top = re.sub(r'(<item objectid="%s"[^>]*transform=")([^"]+)"' % oid, fix, self.top)
        self.cfg = re.sub(r'(<assemble_item object_id="%s" instance_id="0" transform=")([^"]+)"' % oid,
                          fix, self.cfg)
        return oid

    def drop_object(self, name):
        oid = self.object_id(name)
        ent = self.entry_of(oid)
        self.top = re.sub(r'\s*<object id="%s".*?</object>' % oid, '', self.top, flags=re.S)
        self.top = re.sub(r'\s*<item objectid="%s".*?/>' % oid, '', self.top, flags=re.S)
        self.cfg = re.sub(r'\s*<object id="%s">.*?</object>' % oid, '', self.cfg, flags=re.S)
        self.cfg = re.sub(r'\s*<model_instance>\s*<metadata key="object_id" value="%s"/>.*?</model_instance>' % oid,
                          '', self.cfg, flags=re.S)
        self.cfg = re.sub(r'\s*<assemble_item object_id="%s".*?/>' % oid, '', self.cfg, flags=re.S)
        self.files.pop(ent, None)
        return ent

    def add_object(self, name, V, F, extruder, plate, pos, code=None):
        """A new standalone object on plate `plate`, centred at (x,y) of the bed."""
        path, inner = self.add_mesh_file(V, F, code)
        outer = self.next_id; self.next_id += 1
        obj = (f'  <object id="{outer}" p:UUID="{uuid.uuid4()}" type="model">\n   <components>\n'
               f'    <component p:path="/{path}" objectid="{inner}" p:UUID="{uuid.uuid4()}" '
               f'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
               f'   </components>\n  </object>\n')
        self.top = self.top.replace(' </resources>', obj + ' </resources>', 1)
        item = (f'  <item objectid="{outer}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 '
                f'{pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f}" printable="1"/>\n')
        self.top = self.top.replace(' </build>', item + ' </build>', 1)
        cfgobj = (f'  <object id="{outer}">\n'
                  f'    <metadata key="name" value="{name}"/>\n'
                  f'    <metadata key="extruder" value="{extruder}"/>\n'
                  f'    <part id="{inner}" subtype="normal_part">\n'
                  f'      <metadata key="name" value="{name}"/>\n'
                  f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                  f'    </part>\n  </object>\n')
        self.cfg = self.cfg.replace('  <plate>', cfgobj + '  <plate>', 1)
        blocks = self.cfg.split('  <plate>')
        for i, b in enumerate(blocks):
            if f'key="plater_id" value="{plate}"' in b:
                ins = (f'    <model_instance>\n      <metadata key="object_id" value="{outer}"/>\n'
                       f'      <metadata key="instance_id" value="0"/>\n'
                       f'    </model_instance>\n')
                k = b.rfind('</model_instance>')
                if k < 0:
                    k = b.rfind('</plate>')
                    blocks[i] = b[:k] + ins + b[k:]
                else:
                    k += len('</model_instance>\n')
                    blocks[i] = b[:k] + ins + b[k:]
        self.cfg = '  <plate>'.join(blocks)
        return outer

    def replace_mesh(self, obj_name, V, F, codes=None):
        """Replace an object's mesh wholesale: the id inside the file is kept, so no
                reference breaks. Pass paint as an array of codes per face."""
        oid = self.object_id(obj_name)
        ent = self.entry_of(oid)
        inner = int(re.search(r'<object id="(\d+)"', self.files[ent].decode()).group(1))
        self.files[ent] = mesh_xml(V, F, inner, codes)
        n = len(F)
        self.cfg = re.sub(r'(<object id="%s">.*?<metadata face_count=")\d+(")' % oid,
                          lambda m: m.group(1) + str(n) + m.group(2), self.cfg, flags=re.S)
        self.cfg = re.sub(r'(<part id="%s".*?<mesh_stat face_count=")\d+(")' % inner,
                          lambda m: m.group(1) + str(n) + m.group(2), self.cfg, flags=re.S)
        return inner

    def drop_plate(self, plate_id):
        blocks = self.cfg.split('  <plate>')
        out = [blocks[0]]
        for b in blocks[1:]:
            if f'key="plater_id" value="{plate_id}"' in b:
                tail = b[b.index('</plate>') + len('</plate>'):]
                out[-1] = out[-1] + tail.lstrip('\n')
                continue
            out.append(b)
        self.cfg = '  <plate>'.join(out)

    def save(self, dst):
        self.files['3D/3dmodel.model'] = self.top.encode()
        self.files['Metadata/model_settings.config'] = self.cfg.encode()
        rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n')
        for i, p in enumerate(sorted(n for n in self.files if n.startswith('3D/Objects/')), 1):
            rels += (f' <Relationship Target="/{p}" Id="rel-{i}" '
                     f'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n')
        rels += '</Relationships>\n'
        self.files['3D/_rels/3dmodel.model.rels'] = rels.encode()
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as w:
            for n, data in self.files.items():
                w.writestr(n, data)


# ---------------------------------------------------------------- CLI
def make_mesh(spec):
    k = spec['kind']
    if k == 'box':
        return box(spec['lo'], spec['hi'])
    if k == 'cylinder':
        return cylinder(spec['c'], spec['axis'], spec['r'], spec['t0'], spec['t1'],
                        spec.get('seg', 96))
    if k == 'sphere':
        return sphere(spec['c'], spec['r'], spec.get('nu', 48), spec.get('nv', 24))
    if k == 'npz':
        d = np.load(spec['path'])
        return d['V'], d['F']
    raise SystemExit(f'неизвестный вид сетки: {k}')


def cmd_list(path):
    import zipfile as _z
    p = Project(path)
    for oid, blk in re.findall(r'<object id="(\d+)">(.*?)</object>', p.cfg, re.S):
        head = blk.split('<part')[0]
        kv = dict(re.findall(r'<metadata key="(\w+)" value="([^"]*)"/>', head))
        parts = re.findall(r'<part id="(\d+)" subtype="(\w+)"[^>]*>(.*?)</part>', blk, re.S)
        print(f'объект {oid}: {kv.get("name","?")} (филамент {kv.get("extruder","?")})')
        for pid, sub, body in parts:
            nm = re.search(r'key="name" value="([^"]*)"', body)
            print(f'   часть {pid:>4} {sub:<15} {nm.group(1) if nm else ""}')
    for blk in re.findall(r'<plate>(.*?)</plate>', p.cfg, re.S):
        pid = re.search(r'plater_id" value="(\d+)"', blk)
        ids = re.findall(r'object_id" value="(\d+)"', blk)
        print(f'тарелка {pid.group(1) if pid else "?"}: объекты {", ".join(ids) or "пусто"}')


def cmd_apply(spec_path):
    spec = json.load(open(spec_path))
    p = Project(spec['src'])
    for op in spec['ops']:
        kind = op['op']
        if kind == 'part':
            V, F = make_mesh(op['mesh'])
            p.add_mesh_part(op['object'], op.get('name', 'часть'), V, F,
                            subtype=op.get('subtype', 'normal_part'))
            print(f'{op["object"]}: часть «{op.get("name","часть")}» '
                  f'({op.get("subtype","normal_part")}), объём {volume(V, F):.1f} мм³')
        elif kind == 'move':
            R = np.array(op.get('R', np.eye(3)), float)
            T = np.array(op.get('T', [0, 0, 0]), float)
            assert abs(np.linalg.det(R) - 1) < 1e-6, f'det R = {np.linalg.det(R)}'
            n = p.move_object_as_part(op['from'], op['to'], R, T, op.get('name'))
            print(f'{op["from"]} -> часть объекта {op["to"]}: вершин {n}')
        elif kind == 'drop':
            p.drop_object(op['name']); print(f'объект «{op["name"]}» убран')
        elif kind == 'shift':
            p.shift_object(op['name'], op['d']); print(f'{op["name"]} сдвинут на {op["d"]}')
        elif kind == 'object':
            V, F = make_mesh(op['mesh'])
            p.add_object(op['name'], V, F, op.get('extruder', 1), op.get('plate', 1),
                         op.get('pos', (128, 128, 0)))
            print(f'новый объект «{op["name"]}» на тарелке {op.get("plate",1)}, '
                  f'объём {volume(V, F):.1f} мм³')
        elif kind == 'drop_plate':
            p.drop_plate(op['id']); print(f'тарелка {op["id"]} убрана')
        else:
            raise SystemExit(f'неизвестная операция: {kind}')
    p.save(spec['dst'])
    print('->', spec['dst'])


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('list', help='объекты, части и тарелки проекта')
    q.add_argument('src')
    q = sub.add_parser('apply', help='применить задание JSON')
    q.add_argument('spec')
    a = ap.parse_args()
    if a.cmd == 'list':
        cmd_list(a.src)
    else:
        cmd_apply(a.spec)
