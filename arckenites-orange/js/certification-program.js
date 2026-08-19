(function () {
  const domains = [
    {
      id: 'aws', name: 'AWS Cloud', icon: 'fa-brands fa-aws', img: 'assets/aws-logo.png',
      desc: 'Official Amazon Web Services certification track, from Cloud Practitioner to Specialty.',
      tiers: [
        { tier: 'Foundational', certs: [
          { name: 'AWS Certified Cloud Practitioner', code: 'CLF-C02', exp: '0-6 months exposure', difficulty: 'Beginner', duration: '4 Weeks' },
          { name: 'AWS Certified AI Practitioner', code: 'AIF-C01', exp: '0-6 months exposure', difficulty: 'Beginner', duration: '4 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Solutions Architect - Associate', code: 'SAA-C03', exp: '~1 year hands-on (self-paced labs count)', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Developer - Associate', code: 'DVA-C02', exp: '~1 year coding + AWS exposure', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'SysOps Administrator - Associate', code: 'SOA-C02', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Data Engineer - Associate', code: 'DEA-C01', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Machine Learning Engineer - Associate', code: 'MLA-C01', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Professional', certs: [
          { name: 'Solutions Architect - Professional', code: 'SAP-C02', exp: '2+ years designing on AWS', difficulty: 'Advanced', duration: '10 Weeks' },
          { name: 'DevOps Engineer - Professional', code: 'DOP-C02', exp: '2+ years on AWS', difficulty: 'Advanced', duration: '10 Weeks' }
        ]},
        { tier: 'Specialty', certs: [
          { name: 'Security Specialty', code: 'SCS-C02', exp: '2+ years in that specific domain', difficulty: 'Advanced', duration: '6 Weeks' },
          { name: 'Advanced Networking Specialty', code: 'ANS-C01', exp: '2+ years in that specific domain', difficulty: 'Advanced', duration: '6 Weeks' }
        ]}
      ]
    },
    {
      id: 'azure', name: 'Microsoft Azure', icon: 'fa-brands fa-microsoft', img: 'assets/microsoft-logo.png',
      desc: 'Official Microsoft Azure track, from Fundamentals to Expert-level roles.',
      tiers: [
        { tier: 'Fundamentals', certs: [
          { name: 'Azure Fundamentals', code: 'AZ-900', exp: '0-6 months', difficulty: 'Beginner', duration: '3 Weeks' },
          { name: 'Azure AI Fundamentals', code: 'AI-900', exp: '0-6 months', difficulty: 'Beginner', duration: '3 Weeks' },
          { name: 'Azure Data Fundamentals', code: 'DP-900', exp: '0-6 months', difficulty: 'Beginner', duration: '3 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Azure Administrator Associate', code: 'AZ-104', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure Developer Associate', code: 'AZ-204', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure Security Engineer Associate', code: 'AZ-500', exp: '~1 year in security/IT', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure Data Engineer Associate', code: 'DP-203', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure AI Engineer Associate', code: 'AI-102', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Expert', certs: [
          { name: 'Azure Solutions Architect Expert', code: 'AZ-305', exp: '2-3+ years', difficulty: 'Advanced', duration: '10 Weeks' },
          { name: 'Azure DevOps Engineer Expert', code: 'AZ-400', exp: '2-3+ years, usually after AZ-104/AZ-204', difficulty: 'Advanced', duration: '10 Weeks' }
        ]}
      ]
    },
    {
      id: 'network', name: 'Networking', icon: 'fa-solid fa-network-wired',
      desc: 'Cisco and Juniper-backed networking certifications for every experience level.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'CCST - Cisco Certified Support Technician', code: 'Cisco', exp: '0 years, entry point', difficulty: 'Beginner', duration: '3 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'CCNA - Cisco Certified Network Associate', code: 'Cisco', exp: '0-1 year (no formal prereq, ~1 yr recommended)', difficulty: 'Beginner', duration: '8 Weeks' },
          { name: 'CompTIA Network+', code: 'CompTIA', exp: '9-12 months IT support recommended', difficulty: 'Intermediate', duration: '6 Weeks' }
        ]},
        { tier: 'Mid-Level', certs: [
          { name: 'JNCIA / JNCIS (Juniper)', code: 'Juniper', exp: '1-3 years', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Professional', certs: [
          { name: 'CCNP - Cisco Certified Network Professional', code: 'Cisco', exp: '3-5 years', difficulty: 'Advanced', duration: '10 Weeks' }
        ]}
      ]
    },
    {
      id: 'cyber', name: 'Cybersecurity', icon: 'fa-solid fa-shield-halved',
      desc: 'Defend networks and systems with industry-standard security tracks.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'CompTIA Security+', code: 'CompTIA', exp: '~2 years general IT (soft recommendation, not enforced)', difficulty: 'Beginner', duration: '6 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Certified Ethical Hacker (CEH)', code: 'EC-Council', exp: '2 years info-sec (waived via accredited course)', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'CompTIA CySA+', code: 'CompTIA', exp: '3-4 years security experience recommended', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Specialty', certs: [
          { name: 'AWS / Azure Security Specialty', code: 'AWS / MS', exp: '2+ years', difficulty: 'Advanced', duration: '6 Weeks' }
        ]},
        { tier: 'Mandatory Experience', certs: [
          { name: 'CISSP', code: '(ISC)²', exp: '5 years paid work in 2+ security domains (mandatory)', difficulty: 'Advanced', duration: '12 Weeks' },
          { name: 'CISM', code: 'ISACA', exp: '5 years info-sec management experience (mandatory)', difficulty: 'Advanced', duration: '12 Weeks' }
        ]}
      ]
    },
    {
      id: 'data', name: 'Data Science', icon: 'fa-solid fa-chart-line',
      desc: 'Turn data into decisions with analytics, BI and data-engineering tracks.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'Google Data Analytics Professional Certificate', code: 'Google', exp: '0 years - designed for beginners', difficulty: 'Beginner', duration: '8 Weeks' },
          { name: 'IBM Data Science Professional Certificate', code: 'IBM', exp: '0 years', difficulty: 'Beginner', duration: '8 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Power BI Data Analyst Associate', code: 'PL-300', exp: '0-1 year', difficulty: 'Intermediate', duration: '6 Weeks' },
          { name: 'AWS Data Engineer - Associate', code: 'DEA-C01', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Mid-Level', certs: [
          { name: 'Cloudera Data Analyst / Data Engineer', code: 'Cloudera', exp: '1-2 years', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'SAS Certified Data Scientist', code: 'SAS', exp: '1-3 years', difficulty: 'Advanced', duration: '10 Weeks' }
        ]}
      ]
    },
    {
      id: 'ai', name: 'Artificial Intelligence', icon: 'fa-solid fa-brain',
      desc: 'Design and deploy real-world machine learning systems.',
      tiers: [
        { tier: 'Foundational', certs: [
          { name: 'AWS Certified AI Practitioner', code: 'AIF-C01', exp: '0-6 months', difficulty: 'Beginner', duration: '4 Weeks' },
          { name: 'Azure AI Fundamentals', code: 'AI-900', exp: '0-6 months', difficulty: 'Beginner', duration: '3 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'AWS Machine Learning Engineer - Associate', code: 'MLA-C01', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure AI Engineer Associate', code: 'AI-102', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'TensorFlow Developer Certificate', code: 'Google', exp: '0-1 year, portfolio-based', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Professional', certs: [
          { name: 'Google Cloud Associate/Professional ML Engineer', code: 'Google Cloud', exp: '1-3 years', difficulty: 'Advanced', duration: '10 Weeks' },
          { name: 'NVIDIA DLI Certificates', code: 'NVIDIA', exp: 'Varies by course', difficulty: 'Advanced', duration: '6 Weeks' }
        ]}
      ]
    },
    {
      id: 'swe', name: 'Software Engineering', icon: 'fa-solid fa-code',
      desc: 'Ship production-grade applications the enterprise way.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'ISTQB Certified Tester - Foundation Level', code: 'ISTQB', exp: '0 years', difficulty: 'Beginner', duration: '4 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Oracle Certified Professional: Java', code: 'Oracle', exp: '0-1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'AWS Certified Developer - Associate', code: 'DVA-C02', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Azure Developer Associate', code: 'AZ-204', exp: '~1 year', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Mid-Level', certs: [
          { name: 'Certified Scrum Developer (CSD)', code: 'Scrum Alliance', exp: '0-2 years', difficulty: 'Intermediate', duration: '4 Weeks' }
        ]}
      ]
    },
    {
      id: 'devops', name: 'DevOps', icon: 'fa-solid fa-infinity',
      desc: 'Automate delivery pipelines with modern DevOps and IaC tooling.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'Docker Certified Associate (DCA)', code: 'Docker/Mirantis', exp: '0-1 year', difficulty: 'Beginner', duration: '6 Weeks' },
          { name: 'HashiCorp Terraform Associate', code: 'HashiCorp', exp: '0.5-1 year', difficulty: 'Beginner', duration: '6 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Certified Kubernetes Administrator (CKA)', code: 'CNCF', exp: '1-2 years hands-on', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Certified Kubernetes Application Developer (CKAD)', code: 'CNCF', exp: '1-2 years', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Professional', certs: [
          { name: 'AWS DevOps Engineer - Professional', code: 'DOP-C02', exp: '2+ years', difficulty: 'Advanced', duration: '10 Weeks' },
          { name: 'Azure DevOps Engineer Expert', code: 'AZ-400', exp: '2-3+ years', difficulty: 'Advanced', duration: '10 Weeks' }
        ]}
      ]
    },
    {
      id: 'blockchain', name: 'Blockchain', icon: 'fa-solid fa-cubes',
      desc: 'Build decentralized applications and smart contracts.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'Certified Blockchain Professional (CBP)', code: 'Blockchain Council', exp: '0 years', difficulty: 'Beginner', duration: '4 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Certified Blockchain Developer (CBD)', code: 'Blockchain Council', exp: '0-1 year, programming background helps', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Certified Ethereum Developer', code: 'Blockchain Council', exp: '0-1 year, Solidity basics', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Mid-Level', certs: [
          { name: 'R3 Corda / Hyperledger', code: 'R3 / Linux Foundation', exp: '1-2 years', difficulty: 'Advanced', duration: '8 Weeks' }
        ]}
      ]
    },
    {
      id: 'iot', name: 'IoT', icon: 'fa-solid fa-microchip',
      desc: 'Connect devices and build intelligent embedded systems.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'Cisco Certified DevNet Associate / IoT Modules', code: 'Cisco', exp: '0-1 year', difficulty: 'Beginner', duration: '6 Weeks' },
          { name: 'Certified IoT Practitioner (CIoTP)', code: 'CertNexus', exp: '0-1 year, most direct standalone IoT cert', difficulty: 'Beginner', duration: '6 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'AWS Certified IoT (specialty content)', code: 'AWS', exp: '~1 year AWS + embedded basics', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]},
        { tier: 'Mid-Level', certs: [
          { name: 'Certified IoT Security Practitioner (CIoTSP)', code: 'CertNexus', exp: '1-2 years, security-focused IoT', difficulty: 'Advanced', duration: '8 Weeks' }
        ]}
      ]
    },
    {
      id: 'quantum', name: 'Quantum Computing', icon: 'fa-solid fa-atom',
      desc: 'Get ahead of the curve with next-generation computing skills.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'IBM Quantum Certified Associate Developer', code: 'IBM', exp: '0-1 year, needs linear algebra & Python', difficulty: 'Intermediate', duration: '8 Weeks' },
          { name: 'Microsoft Quantum Development Kit / Q# Learning Paths', code: 'Microsoft', exp: '0-1 year', difficulty: 'Intermediate', duration: '6 Weeks' },
          { name: 'Quantum Computing Certificates', code: 'edX / MIT / Coursera', exp: '0-1 year, math-heavy', difficulty: 'Intermediate', duration: '8 Weeks' }
        ]}
      ]
    },
    {
      id: 'marketing', name: 'Digital Marketing', icon: 'fa-solid fa-bullhorn',
      desc: 'Beginner-friendly, mostly-free certifications from Google, Meta and HubSpot.',
      tiers: [
        { tier: 'Entry', certs: [
          { name: 'Google Digital Marketing & E-commerce Certificate', code: 'Google', exp: '0 years', difficulty: 'Beginner', duration: '6 Weeks' },
          { name: 'Google Ads Certification', code: 'Google', exp: '0 years, free', difficulty: 'Beginner', duration: '3 Weeks' },
          { name: 'HubSpot Content / Inbound / Social Media Certifications', code: 'HubSpot', exp: '0 years, free', difficulty: 'Beginner', duration: '3 Weeks' },
          { name: 'Meta Certified Digital Marketing Associate', code: 'Meta', exp: '0 years', difficulty: 'Beginner', duration: '4 Weeks' }
        ]},
        { tier: 'Associate', certs: [
          { name: 'Facebook Blueprint Certification', code: 'Meta', exp: '0-1 year running paid ads', difficulty: 'Intermediate', duration: '4 Weeks' },
          { name: 'SEMrush / Moz SEO Certifications', code: 'SEMrush / Moz', exp: '0-1 year', difficulty: 'Intermediate', duration: '4 Weeks' }
        ]}
      ]
    }
  ];

  function certCardHTML(domain, tierName, cert) {
    return `
      <div class="roadmap-cert-card">
        <h6>${cert.name}</h6>
        <span class="roadmap-cert-code">${cert.code}</span>
        <ul class="roadmap-cert-meta">
          <li><i class="fa-solid fa-user-graduate"></i> ${cert.exp}</li>
          <li><i class="fa-solid fa-signal"></i> ${cert.difficulty}</li>
          <li><i class="fa-regular fa-clock"></i> ${cert.duration}</li>
        </ul>
        <button type="button" class="btn-primary-outline roadmap-view-btn"
          data-domain="${domain.name}" data-tier="${tierName}"
          data-name="${cert.name}" data-code="${cert.code}"
          data-exp="${cert.exp}" data-difficulty="${cert.difficulty}" data-duration="${cert.duration}">
          View Details
        </button>
      </div>
    `;
  }

  function renderDomainCards() {
    const grid = document.getElementById('domainGrid');
    if (!grid) return;
    grid.innerHTML = domains.map((d, i) => `
      <div class="col-sm-6 col-lg-3" data-aos="fade-up" data-aos-delay="${(i % 4) * 80}">
        <div class="domain-card">
          <div class="domain-card__icon ${d.img ? 'domain-card__icon--logo' : ''}">${d.img ? `<img src="${d.img}" alt="${d.name} logo">` : `<i class="${d.icon}"></i>`}</div>
          <h6>${d.name}</h6>
          <p>${d.desc}</p>
          <button type="button" class="btn-primary-outline domain-view-roadmap" data-domain="${d.id}">
            View Roadmap <i class="fa-solid fa-arrow-right"></i>
          </button>
        </div>
      </div>
    `).join('');
  }

  function renderDomainTabs(activeId) {
    const tabs = document.getElementById('roadmapTabs');
    if (!tabs) return;
    tabs.innerHTML = domains.map(d => `
      <button type="button" class="roadmap-tab ${d.id === activeId ? 'active' : ''}" data-domain="${d.id}">
        <i class="${d.icon}"></i> ${d.name}
      </button>
    `).join('');
  }

  function renderRoadmapTree(domainId) {
    const domain = domains.find(d => d.id === domainId) || domains[0];
    const tree = document.getElementById('roadmapTree');
    if (!tree) return;

    tree.innerHTML = domain.tiers.map((t, i) => `
      <div class="roadmap-tier ${i === 0 ? 'open' : ''}">
        <button type="button" class="roadmap-tier__header">
          <span class="roadmap-tier__dot"></span>
          <span class="roadmap-tier__label">${t.tier}</span>
          <span class="roadmap-tier__count">${t.certs.length} Certification${t.certs.length > 1 ? 's' : ''}</span>
          <i class="fa-solid fa-chevron-down roadmap-tier__chevron"></i>
        </button>
        <div class="roadmap-tier__body">
          <div class="roadmap-tier__body-inner">
            ${t.certs.map(c => certCardHTML(domain, t.tier, c)).join('')}
          </div>
        </div>
      </div>
    `).join('');

    tree.querySelectorAll('.roadmap-tier').forEach(tierEl => {
      const header = tierEl.querySelector('.roadmap-tier__header');
      const body = tierEl.querySelector('.roadmap-tier__body');
      if (tierEl.classList.contains('open')) {
        body.style.maxHeight = body.scrollHeight + 'px';
      }
      header.addEventListener('click', () => {
        const isOpen = tierEl.classList.toggle('open');
        body.style.maxHeight = isOpen ? body.scrollHeight + 'px' : '0px';
      });
    });

    tree.querySelectorAll('.roadmap-view-btn').forEach(btn => {
      btn.addEventListener('click', () => openCertModal(btn.dataset));
    });
  }

  function openCertModal(data) {
    const modalEl = document.getElementById('certDetailsModal');
    if (!modalEl || typeof bootstrap === 'undefined') return;
    modalEl.querySelector('.modal-title').textContent = data.name;
    modalEl.querySelector('[data-field="domain"]').textContent = data.domain;
    modalEl.querySelector('[data-field="tier"]').textContent = data.tier;
    modalEl.querySelector('[data-field="code"]').textContent = data.code;
    modalEl.querySelector('[data-field="exp"]').textContent = data.exp;
    modalEl.querySelector('[data-field="difficulty"]').textContent = data.difficulty;
    modalEl.querySelector('[data-field="duration"]').textContent = data.duration;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function setActiveDomain(domainId, scrollIntoView) {
    renderDomainTabs(domainId);
    renderRoadmapTree(domainId);
    document.querySelectorAll('.roadmap-tab').forEach(tab => {
      tab.addEventListener('click', () => setActiveDomain(tab.dataset.domain, false));
    });
    if (scrollIntoView) {
      const section = document.getElementById('roadmaps');
      if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderDomainCards();
    setActiveDomain('aws', false);

    document.querySelectorAll('.domain-view-roadmap').forEach(btn => {
      btn.addEventListener('click', () => setActiveDomain(btn.dataset.domain, true));
    });
  });
})();
