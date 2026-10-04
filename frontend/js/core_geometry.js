// SPDX-License-Identifier: Apache-2.0
import * as THREE from 'three';

export function createCore() {
  const group = new THREE.Group();
  const root = new THREE.Mesh(new THREE.IcosahedronGeometry(0.46, 1), new THREE.MeshBasicMaterial({ color: '#57ddd1', wireframe: true }));
  root.add(new THREE.Mesh(new THREE.IcosahedronGeometry(0.3, 0), new THREE.MeshStandardMaterial({ color: '#e8eeed', metalness: 0.8, roughness: 0.3 })));
  group.add(root);
  const rings = [];
  for (let i = 0; i < 6; i++) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.72 + i * 0.16, i % 2 ? 0.012 : 0.025, 8, 100, i % 2 ? Math.PI * 1.65 : Math.PI * 2), new THREE.MeshBasicMaterial({ color: i === 1 || i === 4 ? '#efb36f' : '#57ddd1' }));
    ring.rotation.set(i < 4 ? 0.08 * i : 0.65, i < 4 ? 0 : i * 0.15, i * 0.5);
    group.add(ring); rings.push(ring);
  }
  return { group, root, rings };
}
